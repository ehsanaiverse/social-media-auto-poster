"""Scheduled posts in the workflow (D46, commit 5a): only the confirmed content is ever published.

Each test runs the real graph on temporary folders and a temporary database, with a fake publisher that
counts its calls. Nothing contacts a platform.
"""

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph import nodes, resume
from social_media_auto_poster.graph.checkpoints import open_checkpointer, saved_thread_ids
from social_media_auto_poster.graph.resume import resume_interrupted_jobs
from social_media_auto_poster.graph.state import WorkflowContext, new_job_state
from social_media_auto_poster.graph.workflow import build_workflow, run_job
from social_media_auto_poster.models import (
    JobStatus,
    Platform,
    PostCandidate,
    PublishMode,
    PublishResult,
    ScheduleStatus,
    ValidationResult,
)
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.reconcile import reconcile_interrupted_jobs
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import JobRepository, ScheduleNotSettledError
from social_media_auto_poster.storage.schedules import ScheduleRecord, ScheduleRepository

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
LINKEDIN = Platform.LINKEDIN
REAL_SUCCESS = PublishResult(success=True, simulated=False, platform_post_id="urn:li:share:777")
CHANGED = "content changed after scheduling"


class CountingPublisher:
    def __init__(self, result: PublishResult = REAL_SUCCESS):
        self.result = result
        self.calls = 0

    def publish(self, post, /):
        self.calls += 1
        return self.result


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(base_dir=tmp_path, data_dir=tmp_path / "data")


@pytest.fixture
def conn(settings):
    connection = open_app_db(settings.app_db_path)
    yield connection
    connection.close()


@pytest.fixture
def jobs(conn) -> JobRepository:
    return JobRepository(conn)


@pytest.fixture
def schedules(conn) -> ScheduleRepository:
    return ScheduleRepository(conn)


def make_candidate(settings: Settings, caption: bytes = b"Hello LinkedIn") -> PostCandidate:
    incoming = settings.incoming_dir(LINKEDIN)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "launch.jpg").write_bytes(JPEG)
    (incoming / "launch.txt").write_bytes(caption)
    return PostCandidate(LINKEDIN, "launch", incoming / "launch.jpg", incoming / "launch.txt")


def add_schedule(schedules: ScheduleRepository, candidate: PostCandidate, mode: PublishMode = PublishMode.REAL,
                 *, media_sha256: str | None = None, caption_sha256: str | None = None) -> ScheduleRecord:
    """A SCHEDULED row confirming the files as they are now (or the given hashes)."""
    return schedules.create(
        schedule_id=str(uuid.uuid4()), platform=LINKEDIN, mode=mode, stem=candidate.stem,
        media_name=candidate.media_path.name, caption_name=candidate.caption_path.name,
        media_sha256=media_sha256 or sha256_file(candidate.media_path),
        caption_sha256=caption_sha256 or sha256_file(candidate.caption_path),
        publish_at=datetime.now(UTC) - timedelta(minutes=1),
    )


def make_context(settings, jobs, publisher, mode=PublishMode.REAL, *, schedules=None, checkpointer=None):
    return WorkflowContext(publisher=publisher, mode=mode, repository=jobs, settings=settings,
                           stability_interval_s=0, checkpointer=checkpointer, schedules=schedules)


def job_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM jobs").fetchone()[0]


def in_incoming(candidate: PostCandidate) -> bool:
    return candidate.media_path.exists() and candidate.caption_path.exists()


def failed_folders(settings: Settings) -> list[Path]:
    folder = settings.failed_dir(LINKEDIN)
    return list(folder.iterdir()) if folder.exists() else []


def assert_invalid_untouched(schedules, schedule, conn, publisher, candidate, settings, reason: str) -> None:
    after = schedules.get(schedule.schedule_id)
    assert after.status is ScheduleStatus.INVALID
    assert reason in after.reason
    assert after.job_id is None
    assert job_count(conn) == 0
    assert publisher.calls == 0
    assert failed_folders(settings) == []


# 1. Unchanged content


@pytest.mark.parametrize(("publisher", "mode", "status"), [
    (CountingPublisher(), PublishMode.REAL, JobStatus.PUBLISHED),
    (Simulator(), PublishMode.SIMULATED, JobStatus.SIMULATED),
])
def test_unchanged_scheduled_post_is_processed_and_the_schedule_is_done(settings, conn, jobs, schedules,
                                                                       publisher, mode, status):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate, mode)

    final = run_job(candidate, make_context(settings, jobs, publisher, mode, schedules=schedules), schedule=schedule)

    assert final["status"] == status.value
    after = schedules.get(schedule.schedule_id)
    assert after.status is ScheduleStatus.DONE and after.job_id == final["job_id"]
    assert jobs.get(final["job_id"]).status is status


def test_the_publishing_row_and_done_are_one_transaction(settings, conn, jobs, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    publisher = CountingPublisher()
    real_check = nodes.check_duplicate

    def check_then_cancel(state, runtime):  # The schedule stops being SCHEDULED just before mark_publishing.
        update = real_check(state, runtime)
        schedules.cancel(schedule.schedule_id)
        return update

    monkeypatch.setattr(nodes, "check_duplicate", check_then_cancel)

    with pytest.raises(ScheduleNotSettledError):
        run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert job_count(conn) == 0  # The PUBLISHING insert was rolled back with the failed DONE update.
    assert publisher.calls == 0
    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.CANCELLED


# 2-6. Changed or missing content: INVALID, never published, files left in incoming/


@pytest.mark.parametrize("changed_file", ["image", "caption"])
def test_changed_content_makes_the_schedule_invalid(settings, conn, jobs, schedules, changed_file):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    if changed_file == "image":
        candidate.media_path.write_bytes(JPEG + b"\x01")
    else:
        candidate.caption_path.write_bytes(b"Edited after scheduling")
    publisher = CountingPublisher()

    run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert_invalid_untouched(schedules, schedule, conn, publisher, candidate, settings, CHANGED)
    assert in_incoming(candidate)


def test_changed_content_that_is_now_invalid_is_invalid_not_failed(settings, conn, jobs, schedules):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    candidate.caption_path.write_bytes(b"x" * 3001)  # Too long now: validate would fail it and move the files.
    publisher = CountingPublisher()

    run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert_invalid_untouched(schedules, schedule, conn, publisher, candidate, settings, CHANGED)
    assert in_incoming(candidate)


@pytest.mark.parametrize("deleted", ["launch.jpg", "launch.txt"])
def test_a_deleted_file_makes_the_schedule_invalid(settings, conn, jobs, schedules, deleted):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    (settings.incoming_dir(LINKEDIN) / deleted).unlink()
    publisher = CountingPublisher()

    run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert_invalid_untouched(schedules, schedule, conn, publisher, candidate, settings, f"file missing: {deleted}")


def test_a_file_deleted_between_the_checks_makes_the_schedule_invalid(settings, conn, jobs, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    real_check = nodes.check_schedule

    def check_then_delete(state, runtime):
        update = real_check(state, runtime)
        candidate.caption_path.unlink()
        return update

    monkeypatch.setattr(nodes, "check_schedule", check_then_delete)
    publisher = CountingPublisher()

    run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert_invalid_untouched(schedules, schedule, conn, publisher, candidate, settings, "file missing: launch.txt")


def test_a_file_swapped_between_the_checks_is_caught_by_check_duplicate(settings, conn, jobs, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    real_validate = nodes.validate

    def validate_then_swap(state):  # After check_schedule passed, before check_duplicate hashes again.
        update = real_validate(state)
        candidate.caption_path.write_bytes(b"Swapped in after the first check")
        return update

    monkeypatch.setattr(nodes, "validate", validate_then_swap)
    publisher = CountingPublisher()

    run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert_invalid_untouched(schedules, schedule, conn, publisher, candidate, settings, CHANGED)


# 7-9. Waiting, duplicates, and a validation failure of unchanged content


def test_a_locked_file_leaves_the_schedule_scheduled(settings, conn, jobs, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)

    def locked(path, **kwargs):
        raise PermissionError(13, "locked by another program", str(path))

    monkeypatch.setattr(nodes, "sha256_file", locked)
    publisher = CountingPublisher()

    final = run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert final["retry_later"] is True
    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.SCHEDULED
    assert job_count(conn) == 0 and publisher.calls == 0


def test_a_duplicate_makes_the_schedule_invalid_with_the_reason(settings, conn, jobs, schedules):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    jobs.create(job_id="earlier-job", platform=LINKEDIN, mode=PublishMode.REAL, stem="old", media_name="old.jpg",
                caption_name="old.txt", media_sha256=schedule.media_sha256, caption_sha256=schedule.caption_sha256,
                status=JobStatus.PUBLISHED)
    publisher = CountingPublisher()

    run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    after = schedules.get(schedule.schedule_id)
    assert after.status is ScheduleStatus.INVALID
    assert "duplicate of job earlier- (PUBLISHED)" in after.reason  # The first 8 characters of the job ID.
    assert job_count(conn) == 1 and publisher.calls == 0
    assert in_incoming(candidate)


def test_unchanged_content_that_fails_validation_is_a_failed_job_and_the_schedule_is_done(
        settings, conn, jobs, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    monkeypatch.setattr(nodes, "validate_candidate",
                        lambda c: ValidationResult(is_valid=False, errors=("a rule that changed",)))
    publisher = CountingPublisher()

    final = run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert final["status"] == JobStatus.FAILED.value
    after = schedules.get(schedule.schedule_id)
    assert after.status is ScheduleStatus.DONE and after.job_id == final["job_id"]
    assert jobs.get(final["job_id"]).status is JobStatus.FAILED
    assert publisher.calls == 0


# 10-12. Last checks and unscheduled posts


def test_publish_refuses_hashes_that_differ_from_the_schedule(settings, conn, jobs, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate, media_sha256="a" * 64, caption_sha256="b" * 64)
    # Get past the two earlier checks (as if they had a bug), so only publish's own check stands between
    # the files and the publisher. The stand-in for check_duplicate returns the files' real hashes.
    monkeypatch.setattr(nodes, "check_schedule", lambda state, runtime: {})
    monkeypatch.setattr(nodes, "check_duplicate", lambda state, runtime: {
        "media_sha256": sha256_file(candidate.media_path), "caption_sha256": sha256_file(candidate.caption_path),
    })
    publisher = CountingPublisher()

    final = run_job(candidate, make_context(settings, jobs, publisher, schedules=schedules), schedule=schedule)

    assert final["status"] == JobStatus.FAILED.value
    assert any("differ from the schedule" in error for error in final["errors"])
    assert publisher.calls == 0


def test_a_scheduled_job_without_the_schedules_repository_fails_closed(settings, conn, jobs, schedules):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    publisher = CountingPublisher()

    with pytest.raises(RuntimeError, match="schedules repository"):
        run_job(candidate, make_context(settings, jobs, publisher, schedules=None), schedule=schedule)

    assert job_count(conn) == 0 and publisher.calls == 0
    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.SCHEDULED


def test_an_unscheduled_post_passes_check_schedule_untouched(settings, jobs):
    candidate = make_candidate(settings)
    context = make_context(settings, jobs, Simulator(), PublishMode.SIMULATED)

    updates = build_workflow().stream(new_job_state("job-1", candidate, context.mode), context=context,
                                      stream_mode="updates")
    visited = [name for update in updates for name in update]

    assert visited[:3] == ["check_stability", "check_schedule", "validate"]
    assert visited[-1] == "record_result"


# 13-18. Checkpoints and crashes


def crash(settings: Settings, candidate: PostCandidate, publisher, monkeypatch, *, node: str, after: bool = False,
          schedule: ScheduleRecord | None = None, state=None) -> str:
    """Run one job until ``node`` "kills the process" (optionally right after the node ran). Returns the thread."""
    real_node = getattr(nodes, node)

    def killed(state, runtime=None):
        if after:
            real_node(state, runtime) if runtime is not None else real_node(state)
        raise SystemExit(f"process killed in {node}")

    with monkeypatch.context() as patch:
        patch.setattr(nodes, node, killed)
        conn = open_app_db(settings.app_db_path)
        try:
            with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
                context = make_context(settings, JobRepository(conn), publisher,
                                       schedules=ScheduleRepository(conn), checkpointer=saver)
                with pytest.raises(SystemExit):
                    if state is None:
                        run_job(candidate, context, schedule=schedule)
                    else:
                        build_workflow(saver).invoke(state, {"configurable": {"thread_id": state["job_id"]}},
                                                     context=context, durability="sync")
                [thread] = saved_thread_ids(saver)
        finally:
            conn.close()
    return thread


def resume_now(settings: Settings):
    conn = open_app_db(settings.app_db_path)
    try:
        with open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path) as saver:
            result = resume_interrupted_jobs(saver, JobRepository(conn), settings, schedules=ScheduleRepository(conn))
            left = saved_thread_ids(saver)
    finally:
        conn.close()
    return result, left


def test_an_old_checkpoint_without_the_new_fields_still_resumes(settings, schedules, monkeypatch):
    candidate = make_candidate(settings)
    old_state = new_job_state("old-job", candidate, PublishMode.REAL)
    for key in ("schedule_id", "expected_media_sha256", "expected_caption_sha256", "schedule_problem"):
        old_state.pop(key, None)  # Saved before scheduling existed.
    publisher = CountingPublisher()
    crash(settings, candidate, publisher, monkeypatch, node="record_result", state=old_state)

    result, left = resume_now(settings)

    assert [final["job_id"] for final, _ in result.resumed] == ["old-job"]
    conn = open_app_db(settings.app_db_path)
    try:
        assert JobRepository(conn).get("old-job").status is JobStatus.PUBLISHED
    finally:
        conn.close()
    assert left == [] and publisher.calls == 1


def test_resume_needs_the_required_fields_only():
    full = {key: None for key in resume.JobState.__required_keys__} | {"job_id": "x", "mode": "real"}
    assert "schedule_id" not in full
    assert resume._problem(full, (nodes.RECORD_RESULT,), "x") is None  # No schedule fields: fine.

    without_folder = {k: v for k, v in full.items() if k != "folder_name"}
    assert "missing folder_name" in resume._problem(without_folder, (nodes.RECORD_RESULT,), "x")


def test_a_crash_after_a_mismatch_is_resumed_and_writes_invalid_once(settings, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    candidate.caption_path.write_bytes(b"Edited after scheduling")
    # record_result writes INVALID and then the process dies before its checkpoint: resume runs it again.
    crash(settings, candidate, CountingPublisher(), monkeypatch, node="record_result", after=True, schedule=schedule)
    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.INVALID

    result, left = resume_now(settings)

    assert result.failed == [] and left == []
    after = schedules.get(schedule.schedule_id)
    assert after.status is ScheduleStatus.INVALID and after.reason == CHANGED


def test_a_crash_before_invalid_is_written_is_resumed_and_writes_it(settings, conn, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    candidate.media_path.write_bytes(JPEG + b"\x02")
    crash(settings, candidate, CountingPublisher(), monkeypatch, node="record_result", schedule=schedule)
    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.SCHEDULED

    resume_now(settings)

    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.INVALID
    assert job_count(conn) == 0


def test_a_crash_after_mark_publishing_never_publishes_again(settings, conn, jobs, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    publisher = CountingPublisher()
    job_id = crash(settings, candidate, publisher, monkeypatch, node="publish", schedule=schedule)

    result, left = resume_now(settings)  # Next step is publish: never resumed.
    recoveries = reconcile_interrupted_jobs(jobs, settings)

    assert result.resumed == [] and left == []
    assert [r.job_id for r in recoveries] == [job_id]
    assert jobs.get(job_id).status is JobStatus.NEEDS_REVIEW
    after = schedules.get(schedule.schedule_id)
    assert after.status is ScheduleStatus.DONE and after.job_id == job_id
    assert schedules.due(platform=LINKEDIN, mode=PublishMode.REAL, now=datetime.now(UTC)) == []
    assert publisher.calls == 0


def test_a_crash_before_the_publishing_commit_leaves_the_schedule_scheduled(settings, conn, schedules, monkeypatch):
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    publisher = CountingPublisher()
    crash(settings, candidate, publisher, monkeypatch, node="mark_publishing", schedule=schedule)

    result, left = resume_now(settings)

    assert result.resumed == [] and left == []
    assert job_count(conn) == 0
    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.SCHEDULED
    assert publisher.calls == 0


def test_check_schedule_as_the_next_step_is_never_resumed(settings, conn, schedules, monkeypatch):
    assert nodes.CHECK_SCHEDULE in resume.BEFORE_PUBLISH
    candidate = make_candidate(settings)
    schedule = add_schedule(schedules, candidate)
    crash(settings, candidate, CountingPublisher(), monkeypatch, node="check_schedule", schedule=schedule)

    result, left = resume_now(settings)

    assert result.resumed == [] and left == []
    assert job_count(conn) == 0
    assert schedules.get(schedule.schedule_id).status is ScheduleStatus.SCHEDULED
