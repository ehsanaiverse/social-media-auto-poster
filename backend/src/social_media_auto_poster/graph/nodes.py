"""Graph nodes and routing functions.

Nodes are thin: each calls one or two services and returns a state update.
Every log line carries job ID, platform, stem, and status.

Database rules (Milestone 2):
- The job row is created with status PUBLISHING and committed *before* the
  publish call. If that write fails, the error propagates and nothing is
  published (fail closed).
- ``record_result`` writes the final outcome. If that write fails after a real
  publish, the error propagates, ``archive`` never runs, and the PUBLISHING row
  keeps blocking a second publish until startup reconciliation handles it.
- Duplicates and files still being copied get no row.

Scheduled posts (D46): a job that carries a ``schedule_id`` publishes only the content the
user confirmed. ``check_schedule`` (before validation) and ``check_duplicate`` compare the
fresh hashes with the schedule's; ``publish`` checks once more. A difference, or a missing
file, makes the schedule INVALID in ``record_result``: no job row, the publisher is never
called, and the files stay in incoming/. ``mark_publishing`` creates the PUBLISHING row and
marks the schedule DONE in one transaction.
"""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from social_media_auto_poster.graph.state import JobState, WorkflowContext
from social_media_auto_poster.ingestion.stability import is_file_stable
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate
from social_media_auto_poster.services.archive import ArchiveError, archive_post, write_error_report
from social_media_auto_poster.services.content import ContentNotReadyError, read_post_content
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.jobs import DuplicateJobError
from social_media_auto_poster.storage.schedules import ScheduleNotFoundError, ScheduleRepository

logger = logging.getLogger(__name__)

Update = dict[str, Any]

CONTENT_CHANGED = "content changed after scheduling"

# Node names, shared with workflow.py and the routing functions.
CHECK_STABILITY = "check_stability"
CHECK_SCHEDULE = "check_schedule"
VALIDATE = "validate"
CHECK_DUPLICATE = "check_duplicate"
MARK_PUBLISHING = "mark_publishing"
PUBLISH = "publish"
MOVE_TO_FAILED = "move_to_failed"
RECORD_RESULT = "record_result"
ARCHIVE = "archive"


# Nodes


def check_stability(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """Both files must be fully copied. If not, stop and let a later scan retry."""
    candidate = _candidate(state)
    files = (candidate.media_path, candidate.caption_path)
    if state.get("schedule_id") and not all(path.exists() for path in files):
        # A scheduled post whose file is gone is not "still copying": check_schedule reports it,
        # so the schedule becomes INVALID instead of waiting for a file that is not coming back.
        return {}
    interval = runtime.context.stability_interval_s
    unstable = [p.name for p in files if not is_file_stable(p, interval)]
    if unstable:
        logger.debug("Files still being copied: %s files=%s", _skip_fields(state), unstable)
        return {"retry_later": True}
    return {}


def check_schedule(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """A scheduled post goes on only with exactly the content that was confirmed (D46). Others pass through.

    Runs before validation, so changed content is reported as changed (INVALID, files left in incoming/)
    even if it is now also invalid, instead of becoming a FAILED job moved to failed/.
    """
    if not state.get("schedule_id"):
        return {}
    _schedules(runtime.context)  # Fail closed before anything is written: the outcome must be recordable.
    candidate = _candidate(state)
    missing = _missing_files(candidate)
    if missing:
        return _schedule_problem(state, f"file missing: {missing}")
    try:
        hashes = (sha256_file(candidate.media_path), sha256_file(candidate.caption_path))
    except FileNotFoundError:
        return _schedule_problem(state, f"file missing: {_missing_files(candidate) or 'unknown'}")
    except OSError as error:
        logger.debug("Could not read files to check the schedule: %s error=%s", _skip_fields(state), error)
        return {"retry_later": True}  # Locked: try again next run; the schedule stays SCHEDULED.
    if hashes != (state.get("expected_media_sha256"), state.get("expected_caption_sha256")):
        return _schedule_problem(state, CONTENT_CHANGED)
    return {}


def validate(state: JobState) -> Update:
    if state.get("schedule_id"):
        missing = _missing_files(_candidate(state))
        if missing:  # Gone since check_schedule: an INVALID schedule, not a FAILED job.
            return _schedule_problem(state, f"file missing: {missing}")
    result = validate_candidate(_candidate(state))
    if result.is_valid:
        return {"status": JobStatus.VALIDATED.value}
    update = {"status": JobStatus.FAILED.value, "errors": [*state["errors"], *result.errors]}
    logger.warning("Validation failed: %s errors=%s", _fields({**state, **update}), list(result.errors))
    return update


def check_duplicate(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """Hash both files and look for a live job that already handles this content."""
    candidate = _candidate(state)
    try:
        media_sha256 = sha256_file(candidate.media_path)
        caption_sha256 = sha256_file(candidate.caption_path)
    except FileNotFoundError as error:
        if state.get("schedule_id"):  # Gone since check_schedule: never wait for it.
            return _schedule_problem(state, f"file missing: {_missing_files(candidate) or 'unknown'}")
        logger.debug("Could not read files for hashing: %s error=%s", _skip_fields(state), error)
        return {"retry_later": True}
    except OSError as error:
        # Same meaning as an unstable file: not ready yet, try again next run.
        logger.debug("Could not read files for hashing: %s error=%s", _skip_fields(state), error)
        return {"retry_later": True}

    if state.get("schedule_id") and (media_sha256, caption_sha256) != (
        state.get("expected_media_sha256"), state.get("expected_caption_sha256")
    ):
        return _schedule_problem(state, CONTENT_CHANGED)  # Changed since check_schedule.

    context = runtime.context
    match = context.repository.find_blocking_duplicate(
        platform=candidate.platform,
        mode=context.mode,
        media_sha256=media_sha256,
        caption_sha256=caption_sha256,
    )
    update: Update = {"media_sha256": media_sha256, "caption_sha256": caption_sha256}
    if match is not None:
        update["duplicate_of"] = match.job_id  # Logged once, by record_result.
    return update


def mark_publishing(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """Create the job row as PUBLISHING and commit it before anything is published.

    A separate node so that, once checkpoints exist (M3), PUBLISHING is also
    saved in graph state before the publish call.
    """
    context = runtime.context
    candidate = _candidate(state)
    try:
        context.repository.create(
            job_id=state["job_id"],
            platform=candidate.platform,
            mode=context.mode,
            stem=candidate.stem,
            media_name=candidate.media_path.name,
            caption_name=candidate.caption_path.name,
            media_sha256=state["media_sha256"],
            caption_sha256=state["caption_sha256"],
            status=JobStatus.PUBLISHING,
            errors=tuple(state["errors"]),
            settle_schedule=state.get("schedule_id"),  # Same transaction: the schedule becomes DONE.
        )
    except DuplicateJobError:
        # Another process recorded the same content in the meantime. Never publish twice.
        match = context.repository.find_blocking_duplicate(
            platform=candidate.platform,
            mode=context.mode,
            media_sha256=state["media_sha256"],
            caption_sha256=state["caption_sha256"],
        )
        duplicate_of = match.job_id if match else "unknown"
        logger.debug("Duplicate detected while recording: %s duplicate_of=%s", _skip_fields(state), duplicate_of)
        return {"duplicate_of": duplicate_of}
    return {"status": JobStatus.PUBLISHING.value}


def publish(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """Hand the publisher exactly the bytes the duplicate check hashed, then record its answer."""
    if state.get("schedule_id") and (state["media_sha256"], state["caption_sha256"]) != (
        state.get("expected_media_sha256"), state.get("expected_caption_sha256")
    ):
        # The earlier checks make this impossible; if it ever happens, nothing unconfirmed is sent.
        logger.error("Not published: hashes differ from the schedule: %s", _fields(state))
        message = "not published: the files' hashes differ from the schedule's (content not confirmed)"
        return {"status": JobStatus.FAILED.value, "errors": [*state["errors"], message]}
    try:
        content = read_post_content(
            _candidate(state), media_sha256=state["media_sha256"], caption_sha256=state["caption_sha256"]
        )
    except ContentNotReadyError as error:
        # Nothing was sent, so this is a definite FAILED, never NEEDS_REVIEW.
        logger.warning("Not published, nothing was sent: %s reason=%s", _fields(state), error)
        return {"status": JobStatus.FAILED.value, "errors": [*state["errors"], f"not published: {error}"]}

    publisher = runtime.context.publisher
    try:
        if getattr(publisher, "records_references", False):  # Instagram: the container ID, before media_publish.
            result = publisher.publish(content, record=_reference_recorder(state, runtime.context))
        else:
            result = publisher.publish(content)
    except Exception as error:  # Deliberately broad: any surprise means "outcome unknown".
        logger.exception("Publish outcome unknown; marking NEEDS_REVIEW, not retrying: %s", _fields(state))
        message = f"publish outcome unknown ({type(error).__name__}: {error}); not retried"
        return {"status": JobStatus.NEEDS_REVIEW.value, "errors": [*state["errors"], message]}

    errors = [*state["errors"], result.error] if result.error else state["errors"]
    return {
        "status": result.status.value,
        "simulated": result.simulated,
        "platform_post_id": result.platform_post_id,
        "errors": errors,
    }


def move_to_failed(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """Move a FAILED or NEEDS_REVIEW post out of incoming/ so it is not picked up again."""
    candidate = _candidate(state)
    try:
        folder = archive_post(
            candidate,
            runtime.context.settings.failed_dir(candidate.platform),
            job_id=state["job_id"],
            folder_name=state["folder_name"],
        )
    except (OSError, ArchiveError) as error:
        logger.error("Could not move post to failed/; files left in place: %s error=%s", _fields(state), error)
        return {"errors": [*state["errors"], f"could not move files to failed/: {error}"]}

    try:
        write_error_report(
            folder,
            job_id=state["job_id"],
            stem=state["stem"],
            status=JobStatus(state["status"]),
            errors=state["errors"],
            platform=candidate.platform,
        )
    except OSError as error:
        # The files are already safe in failed/; a missing report must not change the outcome.
        logger.error("Could not write error report: %s folder=%s error=%s", _fields(state), folder, error)
    return {"destination": str(folder)}


def record_result(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """The single place every job's outcome is recorded, in the database and the log.

    Skipped jobs (duplicates, files still being copied) get no row, so their
    log line carries no job_id: a job_id is only ever logged if it is stored.
    A scheduled post whose content changed, whose file is missing, or that is a
    duplicate makes its schedule INVALID here (no job row either).
    """
    if state.get("schedule_problem"):
        _settle_schedule_invalid(state, runtime.context, state["schedule_problem"])
        logger.info(
            "Job skipped: %s status=SKIPPED result=SCHEDULE_INVALID schedule_id=%s reason=%s",
            _skip_fields(state), state["schedule_id"], state["schedule_problem"],
        )
        return {}
    if state["retry_later"]:
        logger.info("Job skipped: %s status=SKIPPED result=WAITING", _skip_fields(state))
        return {}
    if state["duplicate_of"]:
        if state.get("schedule_id"):
            original = runtime.context.repository.get(state["duplicate_of"])
            described = f"{state['duplicate_of'][:8]} ({original.status})" if original else state["duplicate_of"][:8]
            _settle_schedule_invalid(state, runtime.context, f"duplicate of job {described}")
        logger.info(
            "Job skipped: %s status=SKIPPED result=DUPLICATE duplicate_of=%s", _skip_fields(state), state["duplicate_of"]
        )
        return {}

    _write_outcome(state, runtime.context)
    if state["status"] == JobStatus.SIMULATED.value:
        outcome = "SIMULATED: nothing was sent to any platform"
    else:
        outcome = state["status"]
    logger.info(
        "Job finished: %s outcome=%s simulated=%s post_id=%s destination=%s errors=%s",
        _fields(state),
        outcome,
        state["simulated"],
        state["platform_post_id"],
        state["destination"],
        state["errors"],
    )
    return {}


def archive(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """Archive a confirmed real publish. The status stays PUBLISHED even if the move fails."""
    candidate = _candidate(state)
    try:
        folder = archive_post(
            candidate,
            runtime.context.settings.archive_dir(candidate.platform),
            job_id=state["job_id"],
            folder_name=state["folder_name"],
        )
    except (OSError, ArchiveError) as error:
        message = f"published, but could not move files to archive/: {error}"
        logger.error("Published, but could not archive files; left in place: %s error=%s", _fields(state), error)
        errors = [*state["errors"], message]
        _update_after_publish(state, runtime.context, errors=tuple(errors))
        return {"errors": errors}

    logger.info("Archived: %s folder=%s", _fields(state), folder)
    _update_after_publish(state, runtime.context, destination=str(folder))
    return {"destination": str(folder)}


# Routing


# Each router returns a short label. workflow.py maps the label to the next node,
# so the labels are the edge names shown in the workflow diagram.


def route_after_stability(state: JobState) -> str:
    return "waiting" if state["retry_later"] else "stable"


def route_after_schedule_check(state: JobState) -> str:
    if state.get("schedule_problem"):
        return "changed"
    return "waiting" if state["retry_later"] else "confirmed"


def route_after_validate(state: JobState) -> str:
    if state.get("schedule_problem"):
        return "changed"
    return "valid" if state["status"] == JobStatus.VALIDATED.value else "invalid"


def route_after_duplicate_check(state: JobState) -> str:
    if state.get("schedule_problem"):
        return "changed"
    if state["retry_later"]:
        return "waiting"
    return "duplicate" if state["duplicate_of"] else "new"


def route_after_mark_publishing(state: JobState) -> str:
    return "duplicate" if state["duplicate_of"] else "new"


def route_after_publish(state: JobState) -> str:
    if state["status"] in (JobStatus.FAILED.value, JobStatus.NEEDS_REVIEW.value):
        return "failed"
    return "recorded"  # PUBLISHED and SIMULATED are recorded first.


def route_after_record(state: JobState) -> str:
    # Only a confirmed, real publish is archived. SIMULATED files stay in incoming/.
    is_real_publish = state["status"] == JobStatus.PUBLISHED.value and state["simulated"] is False
    return "published" if is_real_publish else "end"


# Helpers


def _write_outcome(state: JobState, context: WorkflowContext) -> None:
    """Create the row if it does not exist yet (validation failures), then store the outcome."""
    repository = context.repository
    status = JobStatus(state["status"])
    if repository.get(state["job_id"]) is None:
        candidate = _candidate(state)
        repository.create(
            job_id=state["job_id"],
            platform=candidate.platform,
            mode=context.mode,
            stem=candidate.stem,
            media_name=candidate.media_path.name,
            caption_name=candidate.caption_path.name,
            media_sha256=state["media_sha256"],
            caption_sha256=state["caption_sha256"],
            status=status,
            settle_schedule=state.get("schedule_id"),  # Same transaction: the schedule becomes DONE.
        )
    repository.update(
        state["job_id"],
        status=status,
        platform_post_id=state["platform_post_id"],
        destination=state["destination"],
        errors=tuple(state["errors"]),
    )


def _reference_recorder(state: JobState, context: WorkflowContext):
    """Writes the publisher's reference to this job's row, committed, before the publish call (D1)."""

    def record(reference: str) -> None:
        context.repository.record_remote_ref(state["job_id"], reference)
        logger.info("Reference recorded before publishing: %s remote_ref=%s", _fields(state), reference)

    return record


def _update_after_publish(state: JobState, context: WorkflowContext, **fields: Any) -> None:
    """Best-effort update once PUBLISHED is already recorded: a failure is logged, never raised."""
    try:
        context.repository.update(state["job_id"], **fields)
    except Exception:  # Deliberately broad: the status is already safe in the database.
        logger.exception("Could not record archive details: %s fields=%s", _fields(state), sorted(fields))


def _schedules(context: WorkflowContext) -> ScheduleRepository:
    if context.schedules is None:
        raise RuntimeError("a scheduled job needs the schedules repository in its context; nothing was done")
    return context.schedules


def _schedule_problem(state: JobState, reason: str) -> Update:
    logger.warning("Scheduled post will not be published: %s schedule_id=%s reason=%s",
                   _skip_fields(state), state["schedule_id"], reason)
    return {"schedule_problem": reason}


def _settle_schedule_invalid(state: JobState, context: WorkflowContext, reason: str) -> None:
    """Mark the job's schedule INVALID. Already final (a resumed record_result running again) is fine."""
    try:
        _schedules(context).mark_invalid(state["schedule_id"], reason=reason)
    except ScheduleNotFoundError:
        logger.info("Schedule already settled: %s schedule_id=%s", _skip_fields(state), state["schedule_id"])


def _missing_files(candidate: PostCandidate) -> str:
    return ", ".join(p.name for p in (candidate.media_path, candidate.caption_path) if not p.exists())


def _candidate(state: JobState) -> PostCandidate:
    return PostCandidate(
        platform=Platform(state["platform"]),
        stem=state["stem"],
        media_path=Path(state["media_path"]),
        caption_path=Path(state["caption_path"]),
    )


def _fields(state: Mapping[str, Any]) -> str:
    """Log fields for a job that is (or is about to be) stored in the database."""
    return (
        f"job_id={state['job_id']} platform={state['platform']} mode={state['mode']} "
        f"stem={state['stem']} status={state['status']}"
    )


def _skip_fields(state: Mapping[str, Any]) -> str:
    """Log fields for a skipped job: no job_id, because skipped jobs are never stored."""
    return f"platform={state['platform']} mode={state['mode']} stem={state['stem']}"
