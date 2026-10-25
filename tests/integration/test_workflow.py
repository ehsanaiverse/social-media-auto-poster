"""Full graph runs on temporary folders with fake publishers. Nothing contacts a platform."""

import json
import logging
import os
from pathlib import Path

import pytest

from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph import nodes
from social_media_auto_poster.graph.state import WorkflowContext, new_job_state
from social_media_auto_poster.graph.workflow import build_workflow, run_job
from social_media_auto_poster.models import Platform, PostCandidate, PublishResult
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.archive import ERROR_REPORT_NAME, ArchiveError

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN


class RecordingPublisher:
    """Returns a fixed result (or raises) and counts calls."""

    def __init__(self, result: PublishResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    def publish(self, post, /):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


REAL_SUCCESS = PublishResult(success=True, simulated=False, platform_post_id="urn:li:share:123")
REAL_FAILURE = PublishResult(success=False, simulated=False, error="LinkedIn rejected the image")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(base_dir=tmp_path, data_dir=tmp_path / "data")


def make_candidate(settings: Settings, media: bytes = JPEG_BYTES, caption: bytes = b"Hello LinkedIn") -> PostCandidate:
    incoming = settings.incoming_dir(LINKEDIN)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "launch.jpg").write_bytes(media)
    (incoming / "launch.txt").write_bytes(caption)
    return PostCandidate(LINKEDIN, "launch", incoming / "launch.jpg", incoming / "launch.txt")


def context(settings: Settings, publisher) -> WorkflowContext:
    return WorkflowContext(publisher=publisher, settings=settings, stability_interval_s=0)


def visited_nodes(candidate: PostCandidate, ctx: WorkflowContext) -> list[str]:
    updates = build_workflow().stream(new_job_state("test-job", candidate), context=ctx, stream_mode="updates")
    return [name for update in updates for name in update]


def only_folder(parent: Path) -> Path:
    folders = list(parent.iterdir())
    assert len(folders) == 1
    return folders[0]


# Safety


def test_langsmith_tracing_is_off():
    # Tracing would send state (paths, captions) to an external service.
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        assert os.environ.get(var, "").strip().lower() not in ("true", "1"), f"{var} must stay off"


# Paths through the graph


def test_valid_post_with_simulator_ends_simulated_and_files_stay(settings):
    candidate = make_candidate(settings)

    final = run_job(candidate, context(settings, Simulator()))

    assert final["status"] == "SIMULATED"
    assert final["simulated"] is True
    assert final["platform_post_id"] is None
    assert final["destination"] is None
    assert final["errors"] == []
    assert candidate.media_path.exists()
    assert candidate.caption_path.exists()
    assert not settings.archive_dir(LINKEDIN).exists()


def test_simulated_run_is_recorded_and_never_archived(settings):
    path = visited_nodes(make_candidate(settings), context(settings, Simulator()))

    assert path == ["check_stability", "validate", "mark_publishing", "publish", "record_result"]


def test_unstable_file_stops_without_touching_anything(settings, monkeypatch):
    candidate = make_candidate(settings)
    monkeypatch.setattr(nodes, "is_file_stable", lambda path, interval: path.suffix != ".txt")
    publisher = RecordingPublisher(REAL_SUCCESS)

    final = run_job(candidate, context(settings, publisher))

    assert final["retry_later"] is True
    assert final["status"] == "DISCOVERED"
    assert publisher.calls == 0
    assert candidate.media_path.exists()
    assert candidate.caption_path.exists()


def test_invalid_post_fails_and_moves_to_failed_with_report(settings):
    candidate = make_candidate(settings, media=b"not an image", caption=b"   ")
    publisher = RecordingPublisher(REAL_SUCCESS)

    final = run_job(candidate, context(settings, publisher))

    assert final["status"] == "FAILED"
    assert final["errors"] == ["image content is not a valid JPEG", "caption is empty"]
    assert publisher.calls == 0
    folder = only_folder(settings.failed_dir(LINKEDIN))
    assert final["destination"] == str(folder)
    assert sorted(p.name for p in folder.iterdir()) == [ERROR_REPORT_NAME, "launch.jpg", "launch.txt"]
    report = (folder / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "Status: FAILED" in report
    assert "- caption is empty" in report
    assert not candidate.media_path.exists()


def test_real_publish_is_recorded_before_archive(settings):
    path = visited_nodes(make_candidate(settings), context(settings, RecordingPublisher(REAL_SUCCESS)))

    assert path == ["check_stability", "validate", "mark_publishing", "publish", "record_result", "archive"]


def test_real_publish_is_archived_with_post_id(settings):
    candidate = make_candidate(settings)

    final = run_job(candidate, context(settings, RecordingPublisher(REAL_SUCCESS)))

    assert final["status"] == "PUBLISHED"
    assert final["simulated"] is False
    assert final["platform_post_id"] == "urn:li:share:123"
    folder = only_folder(settings.archive_dir(LINKEDIN))
    assert final["destination"] == str(folder)
    assert (folder / "launch.jpg").exists()
    assert not candidate.media_path.exists()


def test_definite_publish_failure_moves_to_failed(settings):
    candidate = make_candidate(settings)

    final = run_job(candidate, context(settings, RecordingPublisher(REAL_FAILURE)))

    assert final["status"] == "FAILED"
    assert final["errors"] == ["LinkedIn rejected the image"]
    report = (only_folder(settings.failed_dir(LINKEDIN)) / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "LinkedIn rejected the image" in report
    assert not settings.archive_dir(LINKEDIN).exists()


def test_publisher_exception_means_needs_review_and_no_retry(settings):
    candidate = make_candidate(settings)
    publisher = RecordingPublisher(error=TimeoutError("no response"))

    final = run_job(candidate, context(settings, publisher))

    assert final["status"] == "NEEDS_REVIEW"
    assert publisher.calls == 1
    assert "publish outcome unknown (TimeoutError: no response); not retried" in final["errors"]
    report = (only_folder(settings.failed_dir(LINKEDIN)) / ERROR_REPORT_NAME).read_text(encoding="utf-8")
    assert "Status: NEEDS_REVIEW" in report
    assert "Check LinkedIn before re-posting" in report


# Failures while moving files


def test_archive_failure_after_publish_keeps_published_status(settings, monkeypatch):
    candidate = make_candidate(settings)

    def broken_archive(candidate, dest_dir):
        raise ArchiveError("disk full")

    monkeypatch.setattr(nodes, "archive_post", broken_archive)

    final = run_job(candidate, context(settings, RecordingPublisher(REAL_SUCCESS)))

    assert final["status"] == "PUBLISHED"
    assert final["platform_post_id"] == "urn:li:share:123"
    assert final["destination"] is None
    assert final["errors"] == ["published, but could not move files to archive/: disk full"]
    assert candidate.media_path.exists()


def test_failed_move_to_failed_keeps_status_and_files(settings, monkeypatch):
    candidate = make_candidate(settings, caption=b"")

    def locked_move(candidate, dest_dir):
        raise OSError("locked")

    monkeypatch.setattr(nodes, "archive_post", locked_move)

    final = run_job(candidate, context(settings, Simulator()))

    assert final["status"] == "FAILED"
    assert final["errors"][-1] == "could not move files to failed/: locked"
    assert candidate.media_path.exists()


def test_error_report_failure_is_logged_and_does_not_change_outcome(settings, monkeypatch, caplog):
    candidate = make_candidate(settings, caption=b"")

    def broken_report(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(nodes, "write_error_report", broken_report)

    with caplog.at_level(logging.ERROR):
        final = run_job(candidate, context(settings, Simulator()))

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
def test_final_state_is_json_serializable(settings, publisher, caption):
    final = run_job(make_candidate(settings, caption=caption), context(settings, publisher))

    assert json.loads(json.dumps(final)) == final


def test_record_result_logs_job_fields_and_simulated_label(settings, caplog):
    with caplog.at_level(logging.INFO):
        final = run_job(make_candidate(settings), context(settings, Simulator()))

    finished = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Job finished")]
    assert len(finished) == 1
    assert f"job_id={final['job_id']}" in finished[0]
    assert "platform=LinkedIn" in finished[0]
    assert "status=SIMULATED" in finished[0]
    assert "nothing was sent to any platform" in finished[0]


def test_graph_has_expected_nodes():
    graph = build_workflow().get_graph()

    assert set(graph.nodes) == {
        "__start__", "check_stability", "validate", "mark_publishing", "publish",
        "move_to_failed", "record_result", "archive", "__end__",
    }
