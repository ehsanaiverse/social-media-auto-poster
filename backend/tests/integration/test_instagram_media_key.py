"""Fix 3 (D50): the picture's media host key is committed before the upload, and cleared after a successful delete.

So a picture left on the media host (a crash, a NEEDS_REVIEW outcome, a failed delete) can always be found from
the job row. Unit tests use the step C fakes; the last ones run the real graph with the real SQLite job database.
"""

import sqlite3
from pathlib import Path

import httpx
import pytest

from instagram_fake import TOKEN, USER_ID, FakeGraph
from media_host_fake import InMemoryMediaHost
from social_media_auto_poster.config import ensure_data_dirs, load_settings
from social_media_auto_poster.graph.state import WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PostContent, PublishMode
from social_media_auto_poster.platforms.base import PublishOutcomeUnknown
from social_media_auto_poster.platforms.instagram.client import InstagramClient
from social_media_auto_poster.platforms.instagram.publisher import InstagramPublisher
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository

JPEG = (Path(__file__).resolve().parents[1] / "fixtures" / "images" / "baseline-7x5.jpg").read_bytes()


class WatchedHost(InMemoryMediaHost):
    """Records, at each call, what the media key recorder had stored by then."""

    def __init__(self, keys: list, **kwargs):
        super().__init__(**kwargs)
        self.keys = keys
        self.keys_at: dict[str, list] = {}

    def _call(self, operation: str) -> None:
        self.keys_at.setdefault(operation, list(self.keys))
        super()._call(operation)


def _publish(*, host_fails=frozenset(), media_fails=False, **answers):
    graph = FakeGraph()
    graph.answers.update(answers)
    keys: list = []
    host = WatchedHost(keys, fail_on=host_fails)

    def record_media(key):
        if media_fails:
            raise OSError("database is locked")
        keys.append(key)

    post = PostContent(PostCandidate(Platform.INSTAGRAM, "post", Path("post.jpg"), Path("post.txt")),
                       media_sha256="m" * 64, caption_sha256="c" * 64, media=JPEG, caption="Launch day")
    publisher = InstagramPublisher(InstagramClient(TOKEN, user_id=USER_ID, transport=graph.transport()), host,
                                   sleep=lambda seconds: None)
    try:
        result, error = publisher.publish(post, record=lambda ref: None, record_media=record_media), None
    except PublishOutcomeUnknown as raised:
        result, error = None, raised
    return result, error, keys, host, graph


def test_the_key_is_recorded_before_the_upload_and_cleared_after_the_delete():
    result, _, keys, host, _ = _publish()

    assert result.status is JobStatus.PUBLISHED
    assert len(keys) == 2 and keys[0].startswith("ig-tmp/") and keys[1] is None
    assert host.keys_at["put"] == [keys[0]]  # Recorded before the upload started.
    assert host.leftovers() == ()


def test_a_key_that_cannot_be_recorded_means_nothing_is_uploaded():
    result, _, keys, host, graph = _publish(media_fails=True)

    assert result.status is JobStatus.FAILED and "storage key could not be recorded" in result.error
    assert host.calls == [] and graph.count("create") == 0


def test_a_failed_upload_is_cleaned_up_and_its_key_cleared():
    result, _, keys, host, _ = _publish(host_fails={"put"})

    assert result.status is JobStatus.FAILED and host.calls == ["put", "delete"]
    assert keys[-1] is None


def test_a_needs_review_outcome_keeps_the_picture_and_its_key():
    result, error, keys, host, _ = _publish(publish=httpx.ReadTimeout("lost"))

    assert result is None and error is not None
    assert len(keys) == 1 and keys[0] is not None and host.leftovers() == (keys[0],)


def test_a_failed_delete_keeps_the_key_so_the_picture_can_be_found():
    result, _, keys, host, _ = _publish(host_fails={"delete"})

    assert result.status is JobStatus.PUBLISHED
    assert len(keys) == 1 and host.leftovers() == (keys[0],)


# Through the graph, with the real SQLite job database


@pytest.fixture
def settings():
    settings = load_settings()
    ensure_data_dirs(settings)
    return settings


def _run(settings, host, graph):
    incoming = settings.incoming_dir(Platform.INSTAGRAM)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_text("Launch day", encoding="utf-8")
    candidate = PostCandidate(Platform.INSTAGRAM, "launch", incoming / "launch.jpg", incoming / "launch.txt")
    conn = open_app_db(settings.app_db_path)
    try:
        publisher = InstagramPublisher(InstagramClient(TOKEN, user_id=USER_ID, transport=graph.transport()), host,
                                       sleep=lambda seconds: None)
        context = WorkflowContext(publisher=publisher, mode=PublishMode.REAL, repository=JobRepository(conn),
                                  settings=settings, stability_interval_s=0)
        final = run_job(candidate, context)
        return JobRepository(conn).get(final["job_id"])
    finally:
        conn.close()


def _rows(settings):
    other = sqlite3.connect(settings.app_db_path)
    try:
        return other.execute("SELECT status, media_host_key FROM jobs").fetchall()
    finally:
        other.close()


class DatabaseWatchedHost(InMemoryMediaHost):
    def __init__(self, settings, **kwargs):
        super().__init__(**kwargs)
        self.settings = settings
        self.rows_at_put = None

    def put(self, key, data, *, content_type):
        self.rows_at_put = _rows(self.settings)  # What another connection sees when the upload starts.
        super().put(key, data, content_type=content_type)


def test_the_key_is_committed_to_the_job_row_before_the_upload_and_cleared_after(settings):
    host = DatabaseWatchedHost(settings)

    record = _run(settings, host, FakeGraph())

    [(status, key)] = host.rows_at_put
    assert status == "PUBLISHING" and key and key.startswith("ig-tmp/")
    assert record.status is JobStatus.PUBLISHED and record.media_host_key is None


def test_a_needs_review_job_keeps_its_key_in_the_job_row(settings):
    graph = FakeGraph()
    graph.answers["publish"] = httpx.ReadTimeout("lost")
    host = DatabaseWatchedHost(settings)

    record = _run(settings, host, graph)

    assert record.status is JobStatus.NEEDS_REVIEW
    assert record.media_host_key == host.leftovers()[0]
