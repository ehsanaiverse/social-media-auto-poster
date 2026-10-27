"""Graph nodes and routing functions.

Nodes are thin: each calls one service and returns a state update. Every log
line carries job ID, platform, stem, and status.
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

logger = logging.getLogger(__name__)

Update = dict[str, Any]

# Node names, shared with workflow.py and the routing functions.
CHECK_STABILITY = "check_stability"
VALIDATE = "validate"
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
        logger.info("Files still being copied; will retry later: %s files=%s", _fields(state), unstable)
        return {"retry_later": True}
    return {}


def validate(state: JobState) -> Update:
    result = validate_candidate(_candidate(state))
    if result.is_valid:
        return {"status": JobStatus.VALIDATED.value}
    update = {"status": JobStatus.FAILED.value, "errors": [*state["errors"], *result.errors]}
    logger.warning("Validation failed: %s errors=%s", _fields({**state, **update}), list(result.errors))
    return update


def mark_publishing(state: JobState) -> Update:
    # A separate node so that, once checkpoints exist (M3), PUBLISHING is saved
    # before the publish call. A crash mid-publish then shows up as PUBLISHING.
    return {"status": JobStatus.PUBLISHING.value}


def publish(state: JobState, runtime: Runtime[WorkflowContext]) -> Update:
    try:
        result = runtime.context.publisher.publish(_candidate(state))
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
        folder = archive_post(candidate, runtime.context.settings.failed_dir(candidate.platform))
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


def record_result(state: JobState) -> Update:
    """The single place every job's outcome is recorded. In Milestone 2 this also writes the database."""
    if state["retry_later"]:
        outcome = "not processed yet (files still being copied)"
    elif state["status"] == JobStatus.SIMULATED.value:
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
        folder = archive_post(candidate, runtime.context.settings.archive_dir(candidate.platform))
    except (OSError, ArchiveError) as error:
        logger.error("Published, but could not archive files; left in place: %s error=%s", _fields(state), error)
        return {"errors": [*state["errors"], f"published, but could not move files to archive/: {error}"]}
    logger.info("Archived: %s folder=%s", _fields(state), folder)
    return {"destination": str(folder)}


# Routing


def route_after_stability(state: JobState) -> str:
    return RECORD_RESULT if state["retry_later"] else VALIDATE


def route_after_validate(state: JobState) -> str:
    return MARK_PUBLISHING if state["status"] == JobStatus.VALIDATED.value else MOVE_TO_FAILED


def route_after_publish(state: JobState) -> str:
    if state["status"] in (JobStatus.FAILED.value, JobStatus.NEEDS_REVIEW.value):
        return MOVE_TO_FAILED
    return RECORD_RESULT  # PUBLISHED and SIMULATED are recorded first.


def route_after_record(state: JobState) -> str:
    # Only a confirmed, real publish is archived. SIMULATED files stay in incoming/.
    is_real_publish = state["status"] == JobStatus.PUBLISHED.value and state["simulated"] is False
    return ARCHIVE if is_real_publish else "end"


# Helpers


def _candidate(state: JobState) -> PostCandidate:
    return PostCandidate(
        platform=Platform(state["platform"]),
        stem=state["stem"],
        media_path=Path(state["media_path"]),
        caption_path=Path(state["caption_path"]),
    )


def _fields(state: Mapping[str, Any]) -> str:
    return f"job_id={state['job_id']} platform={state['platform']} stem={state['stem']} status={state['status']}"
