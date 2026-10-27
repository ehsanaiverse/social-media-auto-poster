"""Full graph runs on temporary folders and a temporary database, with fake publishers.

Nothing contacts a platform.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.state import WorkflowContext, new_job_state
from social_media_auto_poster.graph.workflow import build_workflow, run_job
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode, PublishResult
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.archive import ERROR_REPORT_NAME, JOB_MARKER_NAME, ArchiveError
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import DuplicateJobError, JobRepository

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN


class RecordingPublisher:
    """Returns a fixed result (or raises), counts calls, and can run a check during the call."""

    def __init__(self, result: PublishResult | None = None, error: Exception | None = None, during=None):
        self.result = result
        self.error = error
        self.during = during
        self.calls = 0

    def publish(self, post, /):
        self.calls += 1
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        return self.result


REAL_SUCCESS = PublishResult(success=True, simulated=False, platform_post_id="urn:li:share:123")
REAL_FAILURE = PublishResult(success=False, simulated=False, error="LinkedIn rejected the image")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(base_dir=tmp_path, data_dir=tmp_path / "data")


@pytest.fixture
def conn(tmp_path):
    connection = open_app_db(tmp_path / "database" / "app.db")
    yield connection
    connection.close()


@pytest.fixture
def repo(conn) -> JobRepository:
    return JobRepository(conn)


@pytest.fixture
def context(settings, repo):
    """Build a run context. Mode follows the publisher unless given."""

    def make(publisher, mode: PublishMode | None = None) -> WorkflowContext:
        if mode is None:
            mode = PublishMode.SIMULATED if isinstance(publisher, Simulator) else PublishMode.REAL
        return WorkflowContext(
            publisher=publisher, mode=mode, repository=repo, settings=settings, stability_interval_s=0
        )

    return make


def make_candidate(settings: Settings, media: bytes = JPEG_BYTES, caption: bytes = b"Hello LinkedIn") -> PostCandidate:
    incoming = settings.incoming_dir(LINKEDIN)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "launch.jpg").write_bytes(media)
    (incoming / "launch.txt").write_bytes(caption)
    return PostCandidate(LINKEDIN, "launch", incoming / "launch.jpg", incoming / "launch.txt")


def visited_nodes(candidate: PostCandidate, ctx: WorkflowContext) -> list[str]:
    updates = build_workflow().stream(new_job_state("test-job", candidate, ctx.mode), context=ctx, stream_mode="updates")
    return [name for update in updates for name in update]


def only_folder(parent: Path) -> Path:
    folders = list(parent.iterdir())
    assert len(folders) == 1
    return folders[0]


def row_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM jobs").fetchone()[0]


def seed_job(repo: JobRepository, candidate: PostCandidate, *, mode=PublishMode.SIMULATED,
             status=JobStatus.SIMULATED, job_id="earlier-job") -> None:
    repo.create(
        job_id=job_id, platform=LINKEDIN, mode=mode, stem=candidate.stem,
        media_name=candidate.media_path.name, caption_name=candidate.caption_path.name,
        media_sha256=sha256_file(candidate.media_path), caption_sha256=sha256_file(candidate.caption_path),
        status=status,
    )


# Safety


def test_langsmith_tracing_is_off():
    # Tracing would send state (paths, captions) to an external service.
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        assert os.environ.get(var, "").strip().lower() not in ("true", "1"), f"{var} must stay off"


# Paths through the graph


def test_valid_post_with_simulator_ends_simulated_and_files_stay(settings, context, repo):
    candidate = make_candidate(settings)

    final = run_job(candidate, context(Simulator()))

    assert final["status"] == "SIMULATED"
    assert final["simulated"] is True
    assert final["platform_post_id"] is None
    assert final["destination"] is None
    assert final["errors"] == []
    assert final["media_sha256"] == sha256_file(candidate.media_path)
    assert candidate.media_path.exists()
    assert candidate.caption_path.exists()
    assert not settings.archive_dir(LINKEDIN).exists()
    row = repo.get(final["job_id"])
    assert row.status is JobStatus.SIMULATED
    assert row.mode is PublishMode.SIMULATED
    assert (row.media_sha256, row.caption_sha256) == (final["media_sha256"], final["caption_sha256"])


def test_simulated_run_is_recorded_and_never_archived(settings, context):
    path = visited_nodes(make_candidate(settings), context(Simulator()))

    assert path == ["check_stability", "validate", "check_duplicate", "mark_publishing", "publish", "record_result"]


def test_unstable_file_stops_without_touching_anything(settings, context, monkeypatch, conn):
    candidate = make_candidate(settings)
    monkeypatch.setattr(nodes, "is_file_stable", lambda path, interval: path.suffix != ".txt")
    publisher = RecordingPublisher(REAL_SUCCESS)

    final = run_job(candidate, context(publisher))

    assert final["retry_later"] is True
    assert final["status"] == "DISCOVERED"
    assert publisher.calls == 0
    assert candidate.media_path.exists()
    assert row_count(conn) == 0


def test_invalid_post_fails_and_moves_to_failed_with_report(settings, context, repo):
    candidate = make_candidate(settings, media=b"not an image", caption=b"   ")
    publisher = RecordingPublisher(REAL_SUCCESS)

    final = run_job(candidate, context(publisher))

    assert final["status"] == "FAILED"
    assert final["errors"] == ["image content is not a valid JPEG", "caption is empty"]
    assert publisher.calls == 0
    folder = only_folder(settings.failed_dir(LINKEDIN))
    assert final["destination"] == str(folder)
    assert sorted(p.name for p in folder.iterdir()) == [JOB_MARKER_NAME, ERROR_REPORT_NAME, "launch.jpg", "launch.txt"]
    report = (folder / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "Status: FAILED" in report
    assert "- caption is empty" in report
    assert not candidate.media_path.exists()
    row = repo.get(final["job_id"])
    assert row.status is JobStatus.FAILED
    assert row.errors == ("image content is not a valid JPEG", "caption is empty")
    assert row.media_sha256 is None
    assert row.destination == str(folder)


def test_real_publish_is_recorded_before_archive(settings, context):
    path = visited_nodes(make_candidate(settings), context(RecordingPublisher(REAL_SUCCESS)))

    assert path[-3:] == ["publish", "record_result", "archive"]


def test_real_publish_is_archived_with_post_id(settings, context, repo):
    candidate = make_candidate(settings)

    final = run_job(candidate, context(RecordingPublisher(REAL_SUCCESS)))

    assert final["status"] == "PUBLISHED"
    assert final["simulated"] is False
    assert final["platform_post_id"] == "urn:li:share:123"
    folder = only_folder(settings.archive_dir(LINKEDIN))
    assert final["destination"] == str(folder)
    assert (folder / "launch.jpg").exists()
    assert not candidate.media_path.exists()
    row = repo.get(final["job_id"])
    assert row.status is JobStatus.PUBLISHED
    assert row.mode is PublishMode.REAL
    assert row.platform_post_id == "urn:li:share:123"
    assert row.destination == str(folder)


def test_definite_publish_failure_moves_to_failed(settings, context, repo):
    candidate = make_candidate(settings)

    final = run_job(candidate, context(RecordingPublisher(REAL_FAILURE)))

    assert final["status"] == "FAILED"
    assert final["errors"] == ["LinkedIn rejected the image"]
    report = (only_folder(settings.failed_dir(LINKEDIN)) / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "LinkedIn rejected the image" in report
    assert not settings.archive_dir(LINKEDIN).exists()
    row = repo.get(final["job_id"])
    assert row.status is JobStatus.FAILED
    assert row.media_sha256 is not None  # It passed check_duplicate before failing.


def test_publisher_exception_means_needs_review_and_no_retry(settings, context, repo):
    candidate = make_candidate(settings)
    publisher = RecordingPublisher(error=TimeoutError("no response"))

    final = run_job(candidate, context(publisher))

    assert final["status"] == "NEEDS_REVIEW"
    assert publisher.calls == 1
    assert "publish outcome unknown (TimeoutError: no response); not retried" in final["errors"]
    report = (only_folder(settings.failed_dir(LINKEDIN)) / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "Status: NEEDS_REVIEW" in report
    assert "Check LinkedIn before re-posting" in report
    assert repo.get(final["job_id"]).status is JobStatus.NEEDS_REVIEW


# Database: the PUBLISHING row comes first


def test_publishing_row_is_committed_before_the_publisher_is_called(settings, context, tmp_path):
    seen = []

    def check_from_a_second_connection():
        # A separate connection only sees committed data.
        other = sqlite3.connect(tmp_path / "database" / "app.db")
        seen.extend(other.execute("SELECT status, mode FROM jobs").fetchall())
        other.close()

    run_job(make_candidate(settings), context(RecordingPublisher(REAL_SUCCESS, during=check_from_a_second_connection)))

    assert seen == [("PUBLISHING", "real")]


def test_database_failure_before_publish_means_nothing_is_published(settings, context, repo, monkeypatch, conn):
    candidate = make_candidate(settings)
    publisher = RecordingPublisher(REAL_SUCCESS)

    def broken_create(**kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(repo, "create", broken_create)

    with pytest.raises(sqlite3.OperationalError):
        run_job(candidate, context(publisher))

    assert publisher.calls == 0
    assert candidate.media_path.exists()
    assert candidate.caption_path.exists()
    assert row_count(conn) == 0


def test_database_failure_after_real_publish_skips_archive_and_keeps_publishing_row(
    settings, context, repo, monkeypatch, conn
):
    candidate = make_candidate(settings)
    real_update = repo.update

    def broken_update(job_id, **fields):
        if fields.get("status") is JobStatus.PUBLISHED:
            raise sqlite3.OperationalError("disk I/O error")
        return real_update(job_id, **fields)

    monkeypatch.setattr(repo, "update", broken_update)

    with pytest.raises(sqlite3.OperationalError):
        run_job(candidate, context(RecordingPublisher(REAL_SUCCESS)))

    assert candidate.media_path.exists()  # Not archived.
    assert conn.execute("SELECT status FROM jobs").fetchall() == [("PUBLISHING",)]  # Still blocks a re-publish.


# Duplicates


def test_duplicate_of_simulated_job_is_skipped_without_a_row(settings, context, repo, conn):
    candidate = make_candidate(settings)
    seed_job(repo, candidate)
    publisher = Simulator()

    final = run_job(candidate, context(publisher))

    assert final["duplicate_of"] == "earlier-job"
    assert final["status"] == "VALIDATED"
    assert row_count(conn) == 1
    assert candidate.media_path.exists()
    assert candidate.caption_path.exists()


def test_duplicate_skips_publish(settings, context, repo):
    candidate = make_candidate(settings)
    seed_job(repo, candidate, mode=PublishMode.REAL, status=JobStatus.PUBLISHED)
    publisher = RecordingPublisher(REAL_SUCCESS)

    path = visited_nodes(candidate, context(publisher))

    assert path == ["check_stability", "validate", "check_duplicate", "record_result"]
    assert publisher.calls == 0


def test_earlier_failed_job_does_not_block(settings, context, repo):
    candidate = make_candidate(settings)
    seed_job(repo, candidate, status=JobStatus.FAILED)

    final = run_job(candidate, context(Simulator()))

    assert final["duplicate_of"] is None
    assert final["status"] == "SIMULATED"


def test_simulated_history_never_blocks_a_real_publish(settings, context, repo):
    candidate = make_candidate(settings)
    seed_job(repo, candidate, mode=PublishMode.SIMULATED, status=JobStatus.SIMULATED)

    final = run_job(candidate, context(RecordingPublisher(REAL_SUCCESS)))

    assert final["duplicate_of"] is None
    assert final["status"] == "PUBLISHED"


def test_duplicate_found_while_recording_is_never_published(settings, context, repo, monkeypatch):
    candidate = make_candidate(settings)
    publisher = RecordingPublisher(REAL_SUCCESS)
    real_create = repo.create

    def racing_create(**kwargs):
        # Another process records the same content just before us.
        real_create(**{**kwargs, "job_id": "other-process-job"})
        raise DuplicateJobError("lost the race")

    monkeypatch.setattr(repo, "create", racing_create)

    final = run_job(candidate, context(publisher))

    assert publisher.calls == 0
    assert final["duplicate_of"] == "other-process-job"
    assert candidate.media_path.exists()


def test_unreadable_file_during_hashing_waits_for_next_run(settings, context, monkeypatch, conn):
    candidate = make_candidate(settings)

    def locked(path, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(nodes, "sha256_file", locked)
    publisher = RecordingPublisher(REAL_SUCCESS)

    final = run_job(candidate, context(publisher))

    assert final["retry_later"] is True
    assert publisher.calls == 0
    assert row_count(conn) == 0
    assert candidate.media_path.exists()


# Failures while moving files


def test_archive_failure_after_publish_keeps_published_status(settings, context, repo, monkeypatch):
    candidate = make_candidate(settings)

    def broken_archive(candidate, dest_dir, **_):
        raise ArchiveError("disk full")

    monkeypatch.setattr(nodes, "archive_post", broken_archive)

    final = run_job(candidate, context(RecordingPublisher(REAL_SUCCESS)))

    assert final["status"] == "PUBLISHED"
    assert final["platform_post_id"] == "urn:li:share:123"
    assert final["destination"] is None
    assert final["errors"] == ["published, but could not move files to archive/: disk full"]
    assert candidate.media_path.exists()
    row = repo.get(final["job_id"])
    assert row.status is JobStatus.PUBLISHED
    assert row.errors == ("published, but could not move files to archive/: disk full",)


def test_database_failure_after_archive_move_is_logged_not_raised(settings, context, repo, monkeypatch, caplog):
    candidate = make_candidate(settings)
    real_update = repo.update

    def broken_destination_update(job_id, **fields):
        if "destination" in fields and fields["destination"] and "archive" in fields["destination"]:
            raise sqlite3.OperationalError("disk I/O error")
        return real_update(job_id, **fields)

    monkeypatch.setattr(repo, "update", broken_destination_update)

    with caplog.at_level(logging.ERROR):
        final = run_job(candidate, context(RecordingPublisher(REAL_SUCCESS)))

    assert final["status"] == "PUBLISHED"
    assert repo.get(final["job_id"]).status is JobStatus.PUBLISHED
    assert only_folder(settings.archive_dir(LINKEDIN)).exists()
    assert any("Could not record archive details" in r.getMessage() for r in caplog.records)


def test_failed_move_to_failed_keeps_status_and_files(settings, context, monkeypatch):
    candidate = make_candidate(settings, caption=b"")

    def locked_move(candidate, dest_dir, **_):
        raise OSError("locked")

    monkeypatch.setattr(nodes, "archive_post", locked_move)

    final = run_job(candidate, context(Simulator()))

    assert final["status"] == "FAILED"
    assert final["errors"][-1] == "could not move files to failed/: locked"
    assert candidate.media_path.exists()


def test_error_report_failure_is_logged_and_does_not_change_outcome(settings, context, monkeypatch, caplog):
    candidate = make_candidate(settings, caption=b"")

    def broken_report(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(nodes, "write_error_report", broken_report)

    with caplog.at_level(logging.ERROR):
        final = run_job(candidate, context(Simulator()))

    assert final["status"] == "FAILED"
    assert final["errors"] == ["caption is empty"]
    folder = only_folder(settings.failed_dir(LINKEDIN))
    assert final["destination"] == str(folder)
    assert (folder / "launch.jpg").exists()
    assert not (folder / ERROR_REPORT_NAME).exists()
    assert any("Could not write error report" in r.getMessage() for r in caplog.records)


# State and logging


@pytest.mark.parametrize(
    ("publisher", "caption"),
    [(Simulator(), b"Hi"), (RecordingPublisher(REAL_SUCCESS), b"Hi"), (RecordingPublisher(error=OSError("x")), b"Hi"),
     (Simulator(), b"")],
    ids=["simulated", "published", "needs-review", "invalid"],
)
def test_final_state_is_json_serializable(settings, context, publisher, caption):
    final = run_job(make_candidate(settings, caption=caption), context(publisher))

    assert json.loads(json.dumps(final)) == final


def test_record_result_logs_job_fields_and_simulated_label(settings, context, caplog):
    with caplog.at_level(logging.INFO):
        final = run_job(make_candidate(settings), context(Simulator()))

    finished = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Job finished")]
    assert len(finished) == 1
    assert f"job_id={final['job_id']}" in finished[0]
    assert "platform=LinkedIn" in finished[0]
    assert "status=SIMULATED" in finished[0]
    assert "nothing was sent to any platform" in finished[0]


def test_graph_has_expected_nodes():
    graph = build_workflow().get_graph()

    assert set(graph.nodes) == {
        "__start__", "check_stability", "validate", "check_duplicate", "mark_publishing", "publish",
        "move_to_failed", "record_result", "archive", "__end__",
    }
