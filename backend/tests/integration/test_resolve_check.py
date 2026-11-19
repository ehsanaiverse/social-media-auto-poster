"""D3: ``resolve <job> --check`` settles a NEEDS_REVIEW job by asking the platform, read-only.

Each Instagram job here is made NEEDS_REVIEW by the real graph (media_publish's answer is lost), so its files
are in failed/, its container ID and picture key are recorded, and the picture is still on the media host.
The check then runs against a second fake Graph API whose every request is kept: it must never POST. Real
Instagram stays refused (D49): the tests replace ``_remote_checker`` and ``_media_host``, and one test shows
that without them the check is refused.
"""

import sqlite3
from pathlib import Path

import httpx
import pytest

from instagram_fake import CONTAINER_ID, MEDIA_ID, TOKEN, USER_ID, FakeGraph, json_response
from media_host_fake import InMemoryMediaHost
from social_media_auto_poster import main as cli
from social_media_auto_poster.config import ensure_data_dirs, load_settings
from social_media_auto_poster.graph.state import WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode
from social_media_auto_poster.operations import instagram as instagram_ops
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.publisher import InstagramPublisher
from social_media_auto_poster.platforms.instagram.remote import InstagramRemoteChecker
from social_media_auto_poster.services.archive import ERROR_REPORT_NAME
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()


@pytest.fixture
def settings():
    settings = load_settings()
    ensure_data_dirs(settings)
    return settings


@pytest.fixture
def host():
    return InMemoryMediaHost()


@pytest.fixture
def checker_graph():
    return FakeGraph()


@pytest.fixture
def faked(monkeypatch, settings, host, checker_graph):
    """The check reads from the fake Graph API and deletes from the in-memory host (no network, D49 unchanged)."""
    client = InstagramClient(TOKEN, user_id=USER_ID, transport=checker_graph.transport())
    monkeypatch.setattr(instagram_ops, "_remote_checker", lambda settings: InstagramRemoteChecker(client))
    monkeypatch.setattr(instagram_ops, "_media_host", lambda settings: host)
    return checker_graph


def needs_review_job(settings, host) -> str:
    """A real Instagram job whose media_publish answer was lost: NEEDS_REVIEW, files in failed/, picture kept."""
    incoming = settings.incoming_dir(Platform.INSTAGRAM)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_text("Launch day", encoding="utf-8")
    graph = FakeGraph()
    graph.answers["publish"] = httpx.ReadTimeout("lost")
    conn = open_app_db(settings.app_db_path)
    try:
        publisher = InstagramPublisher(InstagramClient(TOKEN, user_id=USER_ID, transport=graph.transport()), host,
                                       sleep=lambda seconds: None)
        context = WorkflowContext(publisher=publisher, mode=PublishMode.REAL, repository=JobRepository(conn),
                                  settings=settings, stability_interval_s=0)
        final = run_job(PostCandidate(Platform.INSTAGRAM, "launch", incoming / "launch.jpg", incoming / "launch.txt"),
                        context)
    finally:
        conn.close()
    record = stored(settings, final["job_id"])
    assert record.status is JobStatus.NEEDS_REVIEW and record.remote_ref == CONTAINER_ID
    assert record.media_host_key and host.leftovers() == (record.media_host_key,)
    return final["job_id"]


def stored(settings, job_id):
    conn = open_app_db(settings.app_db_path)
    try:
        return JobRepository(conn).get(job_id)
    finally:
        conn.close()


def check(job_id: str) -> int:
    return cli.main(["resolve", job_id[:8], "--check"])


def status(value):
    return json_response(200, {"status_code": value, "id": CONTAINER_ID})


def assert_read_only(graph: FakeGraph) -> None:
    assert graph.requests and all(request.method == "GET" for request in graph.requests)
    assert graph.count("create") == 0 and graph.count("publish") == 0


# PUBLISHED


def test_a_published_container_settles_the_job_as_published_and_archives_its_files(settings, host, faked, capsys):
    job_id = needs_review_job(settings, host)
    failed_folder = Path(stored(settings, job_id).destination)
    faked.answers["status"] = status("PUBLISHED")
    faked.answers["media"] = json_response(200, {"data": [{"id": MEDIA_ID, "media_type": "IMAGE",
                                                           "timestamp": "2099-01-01T00:00:00+0000"}]})

    assert check(job_id) == 0

    record = stored(settings, job_id)
    assert record.status is JobStatus.PUBLISHED and record.platform_post_id == MEDIA_ID
    archived = Path(record.destination)
    assert archived.parent == settings.archive_dir(Platform.INSTAGRAM)
    assert (archived / "launch.jpg").is_file() and not (failed_folder / "launch.jpg").exists()
    assert host.leftovers() == () and record.media_host_key is None
    assert f"Resolved job {job_id[:8]} (launch) as PUBLISHED (post ID {MEDIA_ID})." in capsys.readouterr().out
    assert_read_only(faked)


def test_after_published_the_same_content_is_blocked_as_a_duplicate(settings, host, faked):
    job_id = needs_review_job(settings, host)
    faked.answers["status"] = status("PUBLISHED")
    check(job_id)
    record = stored(settings, job_id)

    conn = open_app_db(settings.app_db_path)
    try:
        blocking = JobRepository(conn).find_blocking_duplicate(
            platform=Platform.INSTAGRAM, mode=PublishMode.REAL,
            media_sha256=record.media_sha256, caption_sha256=record.caption_sha256)
    finally:
        conn.close()
    assert blocking is not None and blocking.job_id == job_id


def test_published_without_a_media_id_is_published_with_no_post_id_and_a_clear_note(settings, host, faked, capsys):
    job_id = needs_review_job(settings, host)
    faked.answers["status"] = status("PUBLISHED")  # The media list is empty: the post cannot be identified.

    assert check(job_id) == 0

    record = stored(settings, job_id)
    assert record.status is JobStatus.PUBLISHED and record.platform_post_id is None
    assert any("no post ID was recorded because the post could not be identified" in e for e in record.errors)
    assert "as PUBLISHED (no post ID: the post could not be identified)." in capsys.readouterr().out


# FAILED


@pytest.mark.parametrize("value", ["ERROR", "EXPIRED"])
def test_an_error_or_expired_container_settles_the_job_as_failed_in_failed_with_a_report(
    settings, host, faked, value
):
    job_id = needs_review_job(settings, host)
    faked.answers["status"] = status(value)

    assert check(job_id) == 0

    record = stored(settings, job_id)
    folder = Path(record.destination)
    assert record.status is JobStatus.FAILED
    assert folder.parent == settings.failed_dir(Platform.INSTAGRAM) and (folder / "launch.jpg").is_file()
    report = (folder / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "Status: FAILED" in report and value in report
    assert host.leftovers() == () and record.media_host_key is None
    assert_read_only(faked)


def test_a_failed_job_no_longer_blocks_its_content(settings, host, faked):
    job_id = needs_review_job(settings, host)
    faked.answers["status"] = status("EXPIRED")
    check(job_id)
    record = stored(settings, job_id)

    conn = open_app_db(settings.app_db_path)
    try:
        assert JobRepository(conn).find_blocking_duplicate(
            platform=Platform.INSTAGRAM, mode=PublishMode.REAL,
            media_sha256=record.media_sha256, caption_sha256=record.caption_sha256) is None
    finally:
        conn.close()


def test_no_recorded_reference_is_failed_without_asking_the_platform(settings, faked):
    conn = open_app_db(settings.app_db_path)
    try:
        repository = JobRepository(conn)
        repository.create(job_id="ig-no-ref-0001", platform=Platform.INSTAGRAM, mode=PublishMode.REAL, stem="lost",
                          media_name="lost.jpg", caption_name="lost.txt", media_sha256="a" * 64,
                          caption_sha256="c" * 64, status=JobStatus.PUBLISHING)
        repository.update("ig-no-ref-0001", status=JobStatus.NEEDS_REVIEW)
    finally:
        conn.close()

    assert check("ig-no-ref-0001") == 0

    assert stored(settings, "ig-no-ref-0001").status is JobStatus.FAILED
    assert faked.requests == []  # Nothing to ask: media_publish is never called without a recorded reference.


# Still uncertain


@pytest.mark.parametrize("answer", [status("FINISHED"), status("IN_PROGRESS"), json_response(500, {})],
                         ids=["finished", "in-progress", "unreadable"])
def test_an_uncertain_answer_changes_nothing_and_exits_3(settings, host, faked, capsys, answer):
    job_id = needs_review_job(settings, host)
    before = stored(settings, job_id)
    faked.answers["status"] = answer

    assert check(job_id) == 3

    assert stored(settings, job_id) == before
    assert host.leftovers() == (before.media_host_key,)
    out = capsys.readouterr().out
    assert "stays NEEDS_REVIEW; nothing was changed" in out and "check again in about" in out
    assert_read_only(faked)


# A failed delete, refusals, the lock


def test_a_failed_delete_never_changes_the_outcome_and_keeps_the_key(settings, host, faked, capsys):
    job_id = needs_review_job(settings, host)
    faked.answers["status"] = status("ERROR")
    host.fail_on.add("delete")

    assert check(job_id) == 0

    record = stored(settings, job_id)
    assert record.status is JobStatus.FAILED and record.media_host_key == host.leftovers()[0]
    assert "could not be deleted (MediaHostError); its key is kept for cleanup" in capsys.readouterr().out


def test_linkedin_has_no_check_and_nothing_changes(settings, capsys):
    conn = open_app_db(settings.app_db_path)
    try:
        repository = JobRepository(conn)
        repository.create(job_id="li-review-0001", platform=Platform.LINKEDIN, mode=PublishMode.REAL, stem="post",
                          media_name="post.jpg", caption_name="post.txt", media_sha256="b" * 64,
                          caption_sha256="c" * 64, status=JobStatus.PUBLISHING)
        repository.update("li-review-0001", status=JobStatus.NEEDS_REVIEW)
        before = repository.get("li-review-0001")
    finally:
        conn.close()

    assert check("li-review-0001") == 1

    assert "LinkedIn offers no way to check whether job li-revie (post) was published" in capsys.readouterr().err
    assert stored(settings, "li-review-0001") == before


def test_without_credentials_the_instagram_check_is_refused(settings, host, capsys):
    job_id = needs_review_job(settings, host)
    before = stored(settings, job_id)

    assert check(job_id) == 1

    assert "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be set in backend\\.env" in capsys.readouterr().err
    assert stored(settings, job_id) == before


def test_the_check_takes_the_run_lock(settings, host, faked, capsys):
    job_id = needs_review_job(settings, host)
    before = stored(settings, job_id)
    faked.answers["status"] = status("PUBLISHED")

    with single_run_lock(settings.lock_file):
        assert check(job_id) == 4

    assert "Another run is in progress" in capsys.readouterr().err
    assert stored(settings, job_id) == before and faked.requests == []


# Secrets


@pytest.mark.parametrize("value", ["PUBLISHED", "ERROR", "FINISHED"])
def test_the_token_never_reaches_output_logs_reports_or_the_database(settings, host, faked, capsys, value):
    job_id = needs_review_job(settings, host)
    faked.answers["status"] = json_response(200, {"status_code": value, "status": TOKEN, "message": TOKEN})

    check(job_id)

    captured = capsys.readouterr()
    assert TOKEN not in captured.out and TOKEN not in captured.err
    for path in settings.base_dir.rglob("*"):
        if path.is_file() and path.suffix != ".jpg":
            assert TOKEN.encode() not in path.read_bytes(), path
    conn = sqlite3.connect(settings.app_db_path)
    try:
        assert TOKEN not in "\n".join(conn.iterdump())
    finally:
        conn.close()
