"""Settle a NEEDS_REVIEW job after checking the platform (``resolve`` and the API's resolve)."""

import logging
import sqlite3
from dataclasses import dataclass

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, PublishMode
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations.common import LOGGER_NAME
from social_media_auto_poster.operations.logs import _run_scope
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db
from social_media_auto_poster.storage.jobs import JobNotFoundError, JobRecord, JobRepository

logger = logging.getLogger(LOGGER_NAME)  # Same name as before the move, so log lines are unchanged.


@dataclass(frozen=True)
class ResolveResult:
    job: JobRecord
    status: JobStatus
    post_id: str | None
    note: str


class ResolveError(Exception):
    """A resolve was refused. ``kind`` is one of: blank_post_id, no_database, not_found, ambiguous,
    not_needs_review, simulated_published, no_longer_needs_review."""

    def __init__(self, kind: str, message: str, details: tuple[str, ...] = ()):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details


def resolve_job(settings: Settings, job_prefix: str, *, published_post_id: str | None = None) -> ResolveResult:
    """Settle a NEEDS_REVIEW job: PUBLISHED with ``published_post_id``, or FAILED (retryable) without it.

    Takes the run lock, so it never changes the database during a run. Raises ResolveError for a
    refusal (nothing is changed) and RunLockHeldError if a run is in progress.
    """
    if published_post_id is not None and not published_post_id.strip():
        raise ResolveError("blank_post_id", "--published needs the platform's post ID (it must not be empty).")
    with single_run_lock(settings.lock_file), _run_scope():
        if not settings.app_db_path.is_file():
            raise ResolveError("no_database", f"no job database at {settings.app_db_path}; nothing to resolve.")
        try:
            conn = open_app_db(settings.app_db_path)
        except (SchemaTooNewError, sqlite3.Error) as error:
            raise ResolveError("no_database", f"could not open the job database: {error}") from None
        try:
            return _resolve_in_database(JobRepository(conn), job_prefix, published_post_id)
        finally:
            conn.close()


def _resolve_in_database(repository: JobRepository, prefix: str, published_post_id: str | None) -> ResolveResult:
    matches = repository.find_by_id_prefix(prefix)
    if not matches:
        raise ResolveError("not_found", f"no job ID starts with '{prefix}'. Nothing was changed.")
    if len(matches) > 1:
        details = tuple(f"  {r.job_id}  {r.stem}  {r.status}" for r in matches)
        raise ResolveError(
            "ambiguous", f"'{prefix}' matches {len(matches)} jobs; use a longer prefix:", details
        )

    [job] = matches
    name = f"job {job.job_id[:8]} ({job.stem})"
    if job.status is not JobStatus.NEEDS_REVIEW:
        raise ResolveError(
            "not_needs_review",
            f"{name} is {job.status}; only NEEDS_REVIEW jobs can be resolved. Nothing was changed.",
        )
    published = published_post_id is not None
    if published and job.mode is PublishMode.SIMULATED:
        raise ResolveError(
            "simulated_published",
            f"{name} is a simulated job; nothing was sent, so it cannot have been published. "
            "Resolve it with --failed. Nothing was changed.",
        )

    status = JobStatus.PUBLISHED if published else JobStatus.FAILED
    post_id = published_post_id.strip() if published else None
    stamp = f"{clock.utc_now():%Y-%m-%dT%H:%M:%SZ}"
    note = (
        f"resolved manually as PUBLISHED (post ID {post_id}) at {stamp}"
        if published
        else f"resolved manually as FAILED at {stamp}"
    )
    try:
        repository.resolve_needs_review(job.job_id, status=status, note=note, platform_post_id=post_id)
    except JobNotFoundError:  # Cannot happen while we hold the lock; refuse rather than guess.
        raise ResolveError("no_longer_needs_review", f"{name} is no longer NEEDS_REVIEW. Nothing was changed.") from None
    logger.info(
        "Job resolved manually: job_id=%s platform=%s mode=%s stem=%s status=%s post_id=%s",
        job.job_id, job.platform, job.mode, job.stem, status, post_id,
    )
    return ResolveResult(job=job, status=status, post_id=post_id, note=note)
