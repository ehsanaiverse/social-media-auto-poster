"""D1: the container ID is committed to the job row before media_publish, and a crash can never publish twice.

The real graph, the real SQLite job database and checkpoint store, and the Instagram publisher from step C
against the fake Graph API and the in-memory media host. Instagram is still not registered (no adapter), so
these jobs are driven through ``run_job`` directly; nothing here reaches a network.
"""

import sqlite3
from pathlib import Path

import pytest

from instagram_fake import CONTAINER_ID, MEDIA_ID, TOKEN, USER_ID, FakeGraph
from media_host_fake import InMemoryMediaHost
from social_media_auto_poster.config import ensure_data_dirs, load_settings
from social_media_auto_poster.graph.checkpoints import open_checkpointer, saved_thread_ids
from social_media_auto_poster.graph.resume import resume_interrupted_jobs
from social_media_auto_poster.graph.state import WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.publisher import InstagramPublisher
from social_media_auto_poster.services.reconcile import NO_REFERENCE_INTERRUPTED, reconcile_interrupted_jobs
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()
INSTAGRAM_ONLY = frozenset({Platform.INSTAGRAM})


@pytest.fixture
def settings():
    settings = load_settings()
    ensure_data_dirs(settings)
    return settings


@pytest.fixture
def graph():
    return FakeGraph()


def _candidate(settings, stem="launch") -> PostCandidate:
    incoming = settings.incoming_dir(Platform.INSTAGRAM)
    (incoming / f"{stem}.jpg").write_bytes(JPEG)
    (incoming / f"{stem}.txt").write_text("Launch day #instagram", encoding="utf-8")
    return PostCandidate(Platform.INSTAGRAM, stem, incoming / f"{stem}.jpg", incoming / f"{stem}.txt")


def _context(settings, conn, graph, saver=None) -> WorkflowContext:
    client = InstagramClient(TOKEN, user_id=USER_ID, transport=graph.transport())
    publisher = InstagramPublisher(client, InMemoryMediaHost(), sleep=lambda seconds: None)
    return WorkflowContext(publisher=publisher, mode=PublishMode.REAL, repository=JobRepository(conn),
                           settings=settings, stability_interval_s=0, checkpointer=saver)


def _stored_ref_seen_by_another_connection(settings) -> object:
    other = sqlite3.connect(settings.app_db_path)
    try:
        return other.execute("SELECT status, remote_ref FROM jobs").fetchall()
    finally:
        other.close()


def test_the_container_id_is_committed_before_media_publish(settings, graph):
    seen_at_publish = []

    def publish(request):
        seen_at_publish.append(_stored_ref_seen_by_another_connection(settings))
        from instagram_fake import DEFAULTS
        return DEFAULTS["publish"]

    graph.answers["publish"] = publish
    conn = open_app_db(settings.app_db_path)
    try:
        final = run_job(_candidate(settings), _context(settings, conn, graph))
        record = JobRepository(conn).get(final["job_id"])
    finally:
        conn.close()

    assert seen_at_publish == [[("PUBLISHING", CONTAINER_ID)]]  # Committed and visible before media_publish.
    assert record.status is JobStatus.PUBLISHED and record.platform_post_id == MEDIA_ID
    assert record.remote_ref == CONTAINER_ID
    assert graph.count("publish") == 1
    assert "archive" in (record.destination or "")


def test_a_real_sqlite_failure_while_recording_is_failed_and_media_publish_is_never_called(settings, graph):
    conn = open_app_db(settings.app_db_path)
    # A real SQLite error on exactly that write (a temporary trigger on this connection only).
    conn.execute("CREATE TEMP TRIGGER refuse_ref BEFORE UPDATE OF remote_ref ON main.jobs "
                 "BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END")
    try:
        final = run_job(_candidate(settings), _context(settings, conn, graph))
        record = JobRepository(conn).get(final["job_id"])
    finally:
        conn.close()

    assert record.status is JobStatus.FAILED
    assert any("container ID could not be recorded (IntegrityError)" in error for error in record.errors)
    assert record.remote_ref is None
    assert graph.count("publish") == 0
    assert "failed" in (record.destination or "")


def _crash(settings, graph, operation: str) -> str:
    """Run one job until the fake Graph API "kills the process" at ``operation``; returns the job ID."""
    graph.answers[operation] = SystemExit(f"process killed during {operation}")
    conn = open_app_db(settings.app_db_path)
    try:
        with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
            with pytest.raises(SystemExit):
                run_job(_candidate(settings), _context(settings, conn, graph, saver))
            [thread] = saved_thread_ids(saver)
    finally:
        conn.close()
    return thread


def _next_run_recovers(settings, graph):
    """What the next run does before processing: resume checkpoints, then reconcile PUBLISHING rows."""
    conn = open_app_db(settings.app_db_path)
    try:
        repository = JobRepository(conn)
        with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
            resumed = resume_interrupted_jobs(saver, repository, settings)
            recoveries = reconcile_interrupted_jobs(repository, settings, skip=resumed.skip_reconcile,
                                                    reference_platforms=INSTAGRAM_ONLY)
            left = saved_thread_ids(saver)
        return resumed, recoveries, repository.list_recent(10), left
    finally:
        conn.close()


def test_a_crash_during_media_publish_with_a_recorded_container_needs_review_and_is_never_published_again(
    settings, graph
):
    job_id = _crash(settings, graph, "publish")
    assert graph.count("publish") == 1
    assert _stored_ref_seen_by_another_connection(settings) == [("PUBLISHING", CONTAINER_ID)]

    resumed, recoveries, [record], left = _next_run_recovers(settings, graph)

    assert resumed.resumed == [] and resumed.failed == []  # The publish step is never resumed.
    assert [recovery.status for recovery in recoveries] == [JobStatus.NEEDS_REVIEW]
    assert record.job_id == job_id and record.status is JobStatus.NEEDS_REVIEW and record.remote_ref == CONTAINER_ID
    assert any(CONTAINER_ID in error for error in record.errors)
    assert graph.count("publish") == 1  # Nothing published it a second time.
    assert left == []


def test_a_crash_before_the_container_was_recorded_is_failed_nothing_was_published(settings, graph):
    job_id = _crash(settings, graph, "create")
    assert _stored_ref_seen_by_another_connection(settings) == [("PUBLISHING", None)]

    resumed, recoveries, [record], left = _next_run_recovers(settings, graph)

    assert [recovery.status for recovery in recoveries] == [JobStatus.FAILED]
    assert record.job_id == job_id and record.status is JobStatus.FAILED and record.remote_ref is None
    assert NO_REFERENCE_INTERRUPTED in record.errors
    assert graph.count("publish") == 0
    assert left == []


def test_a_real_job_of_a_platform_without_references_still_needs_review(settings):
    # LinkedIn records no reference, so a crash while PUBLISHING keeps its NEEDS_REVIEW rule (unchanged).
    conn = open_app_db(settings.app_db_path)
    try:
        repository = JobRepository(conn)
        repository.create(job_id="linkedin-crash", platform=Platform.LINKEDIN, mode=PublishMode.REAL, stem="post",
                          media_name="post.jpg", caption_name="post.txt", media_sha256="a" * 64,
                          caption_sha256="c" * 64, status=JobStatus.PUBLISHING)
        [recovery] = reconcile_interrupted_jobs(repository, settings, reference_platforms=INSTAGRAM_ONLY)
        assert recovery.status is JobStatus.NEEDS_REVIEW
        assert repository.get("linkedin-crash").status is JobStatus.NEEDS_REVIEW
    finally:
        conn.close()


def test_the_run_passes_only_registered_platforms_that_record_references(monkeypatch):
    from social_media_auto_poster.operations import platforms

    assert frozenset(p for p, a in platforms.ADAPTERS.items() if a.records_references) == frozenset()  # Until D2.
