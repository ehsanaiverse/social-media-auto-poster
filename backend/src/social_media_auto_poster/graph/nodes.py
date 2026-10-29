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

logger = logging.getLogger(__name__)

Update = dict[str, Any]

# Node names, shared with workflow.py and the routing functions.
CHECK_STABILITY = "check_stability"
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
    interval = runtime.context.stability_interval_s
    unstable = [p.name for p in (candidate.media_path, candidate.caption_path) if not is_file_stable(p, interval)]
    if unstable:
        logger.debug("Files still being copied: %s files=%s", _skip_fields(state), unstable)
        return {"retry_later": True}
    return {}


def validate(state: JobState) -> Update:
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
    except OSError as error:
        # Same meaning as an unstable file: not ready yet, try again next run.
        logger.debug("Could not read files for hashing: %s error=%s", _skip_fields(state), error)
        return {"retry_later": True}

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
    try:
        content = read_post_content(
            _candidate(state), media_sha256=state["media_sha256"], caption_sha256=state["caption_sha256"]
        )
    except ContentNotReadyError as error:
        # Nothing was sent, so this is a definite FAILED, never NEEDS_REVIEW.
        logger.warning("Not published, nothing was sent: %s reason=%s", _fields(state), error)
        return {"status": JobStatus.FAILED.value, "errors": [*state["errors"], f"not published: {error}"]}

    try:
        result = runtime.context.publisher.publish(content)
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
        )
    except OSError as error:
        # The files are already safe in failed/; a missing report must not change the outcome.
        logger.error("Could not write error report: %s folder=%s error=%s", _fields(state), folder, error)
    return {"destination": str(folder)}


def record_result(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    """The single place every job's outcome is recorded, in the database and the log.

    Skipped jobs (duplicates, files still being copied) get no row, so their
    log line carries no job_id: a job_id is only ever logged if it is stored.
    """
    if state["retry_later"]:
        logger.info("Job skipped: %s status=SKIPPED result=WAITING", _skip_fields(state))
        return {}
    if state["duplicate_of"]:
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


def route_after_validate(state: JobState) -> str:
    return "valid" if state["status"] == JobStatus.VALIDATED.value else "invalid"


def route_after_duplicate_check(state: JobState) -> str:
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
        )
    repository.update(
        state["job_id"],
        status=status,
        platform_post_id=state["platform_post_id"],
        destination=state["destination"],
        errors=tuple(state["errors"]),
    )


def _update_after_publish(state: JobState, context: WorkflowContext, **fields: Any) -> None:
    """Best-effort update once PUBLISHED is already recorded: a failure is logged, never raised."""
    try:
        context.repository.update(state["job_id"], **fields)
    except Exception:  # Deliberately broad: the status is already safe in the database.
        logger.exception("Could not record archive details: %s fields=%s", _fields(state), sorted(fields))


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
