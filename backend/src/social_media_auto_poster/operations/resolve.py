"""Settle a NEEDS_REVIEW job: by hand after checking the platform (``resolve``), or by asking it (``--check``)."""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, PostCandidate, PublishMode
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations.common import LOGGER_NAME, RealPublishingUnavailable
from social_media_auto_poster.operations.logs import _run_scope
from social_media_auto_poster.operations.platforms import adapter_for
from social_media_auto_poster.platforms.rules import display_name
from social_media_auto_poster.services.archive import (
    ERROR_REPORT_NAME,
    JOB_MARKER_NAME,
    ArchiveError,
    archive_post,
    post_folder_name,
    write_error_report,
)
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


def resolve_job(
    settings: Settings, job_prefix: str, *, published_post_id: str | None = None, platform=None
) -> ResolveResult:
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
            return _resolve_in_database(JobRepository(conn), job_prefix, published_post_id, platform)
        finally:
            conn.close()


def _resolve_in_database(
    repository: JobRepository, prefix: str, published_post_id: str | None, platform=None
) -> ResolveResult:
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
    _require_platform(job, platform)
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



@dataclass(frozen=True)
class CheckResult:
    """What ``resolve --check`` found and did. ``status`` is NEEDS_REVIEW when nothing was changed."""

    job: JobRecord
    status: JobStatus
    post_id: str | None
    reason: str  # The platform's answer, in plain words (never a token).
    notes: tuple[str, ...]  # What happened to the files and the stored picture.
    destination: str | None = None


def check_job(settings: Settings, job_prefix: str, *, platform=None) -> CheckResult:
    """Settle a NEEDS_REVIEW job by asking its platform, read-only (D3). Never publishes anything.

    PUBLISHED: the job is PUBLISHED and its files are archived. FAILED (never published): the job is FAILED and its
    files stay in failed/ with an updated error report. Either way the stored picture is deleted and its key cleared.
    Still uncertain: nothing changes. Takes the run lock. Raises ResolveError for a refusal (nothing changed),
    including a platform with no safe way to check (LinkedIn), and RunLockHeldError.
    """
    with single_run_lock(settings.lock_file), _run_scope():
        if not settings.app_db_path.is_file():
            raise ResolveError("no_database", f"no job database at {settings.app_db_path}; nothing to check.")
        try:
            conn = open_app_db(settings.app_db_path)
        except (SchemaTooNewError, sqlite3.Error) as error:
            raise ResolveError("no_database", f"could not open the job database: {error}") from None
        try:
            return _check_in_database(settings, JobRepository(conn), job_prefix, platform)
        finally:
            conn.close()


def _check_in_database(settings: Settings, repository: JobRepository, prefix: str, platform=None) -> CheckResult:
    job = _one_needs_review_job(repository, prefix, platform)
    name = f"job {job.job_id[:8]} ({job.stem})"
    platform = display_name(job.platform)
    if job.mode is PublishMode.SIMULATED:
        raise ResolveError("simulated_check", f"{name} is a simulated job; nothing was sent, so there is nothing to "
                                              "check. Resolve it with --failed. Nothing was changed.")
    adapter = adapter_for(job.platform)
    try:
        checker = adapter.remote_checker(settings)
    except RealPublishingUnavailable as error:
        raise ResolveError("check_unavailable", f"{error}. Nothing was changed.") from None
    if checker is None:
        raise ResolveError(
            "no_check",
            f"{platform} offers no way to check whether {name} was published. Look at {platform} yourself, then "
            "use --published <post ID> or --failed. Nothing was changed.",
        )

    outcome = checker.check(job.remote_ref, job_created_at=job.created_at)
    if outcome.status is JobStatus.NEEDS_REVIEW:
        logger.info("Job checked, still unknown: job_id=%s platform=%s mode=%s stem=%s",
                    job.job_id, job.platform, job.mode, job.stem)
        return CheckResult(job, JobStatus.NEEDS_REVIEW, None, outcome.reason, ())

    stamp = f"{clock.utc_now():%Y-%m-%dT%H:%M:%SZ}"
    note = f"resolved by checking {platform} as {outcome.status} at {stamp}: {outcome.reason}"
    if outcome.status is JobStatus.PUBLISHED and outcome.post_id is None:
        note += "; no post ID was recorded because the post could not be identified"
    try:  # Status first: from here on the row says what the platform said, whatever happens to the files.
        repository.resolve_needs_review(job.job_id, status=outcome.status, note=note, platform_post_id=outcome.post_id)
    except JobNotFoundError:
        raise ResolveError("no_longer_needs_review", f"{name} is no longer NEEDS_REVIEW. Nothing was changed.") from None
    logger.info("Job resolved by checking the platform: job_id=%s platform=%s mode=%s stem=%s status=%s post_id=%s",
                job.job_id, job.platform, job.mode, job.stem, outcome.status, outcome.post_id)
    updated = repository.get(job.job_id)
    destination, notes = _settle_files(settings, repository, updated, outcome.status)
    notes += _forget_picture(settings, repository, updated, adapter)
    return CheckResult(job, outcome.status, outcome.post_id, outcome.reason, notes, destination)


def _require_platform(job: JobRecord, platform) -> None:
    """``resolve --platform X``: refuse a job of another platform (the job ID alone decides otherwise)."""
    if platform is not None and job.platform is not platform:
        raise ResolveError("wrong_platform", f"job {job.job_id[:8]} ({job.stem}) is a {job.platform} job, not "
                                             f"{platform}. Nothing was changed.")


def _one_needs_review_job(repository: JobRepository, prefix: str, platform=None) -> JobRecord:
    matches = repository.find_by_id_prefix(prefix)
    if not matches:
        raise ResolveError("not_found", f"no job ID starts with '{prefix}'. Nothing was changed.")
    if len(matches) > 1:
        details = tuple(f"  {r.job_id}  {r.stem}  {r.status}" for r in matches)
        raise ResolveError("ambiguous", f"'{prefix}' matches {len(matches)} jobs; use a longer prefix:", details)
    [job] = matches
    _require_platform(job, platform)
    if job.status is not JobStatus.NEEDS_REVIEW:
        raise ResolveError("not_needs_review", f"job {job.job_id[:8]} ({job.stem}) is {job.status}; only "
                                               "NEEDS_REVIEW jobs can be resolved. Nothing was changed.")
    return job


def _settle_files(
    settings: Settings, repository: JobRepository, job: JobRecord, status: JobStatus
) -> tuple[str | None, tuple[str, ...]]:
    """PUBLISHED: archive the files (as after a confirmed publish). FAILED: keep them in failed/, report updated."""
    folder = Path(job.destination) if job.destination else settings.incoming_dir(job.platform)
    candidate = PostCandidate(job.platform, job.stem, folder / job.media_name, folder / job.caption_name)
    if not (candidate.media_path.is_file() and candidate.caption_path.is_file()):
        return job.destination, (f"its files were not found in {folder}; nothing was moved",)
    in_failed = job.destination is not None and folder.parent == settings.failed_dir(job.platform)
    notes_from_cleanup: tuple[str, ...] = ()
    if status is JobStatus.FAILED and in_failed:
        target = folder
    else:
        dest_dir = settings.archive_dir(job.platform) if status is JobStatus.PUBLISHED else settings.failed_dir(job.platform)
        try:
            target = archive_post(candidate, dest_dir, job_id=job.job_id,
                                  folder_name=post_folder_name(job.stem, datetime.now(UTC)))
        except (OSError, ArchiveError) as error:
            repository.update(job.job_id, errors=(*job.errors, f"could not move the files: {error}"))
            return job.destination, (f"could not move the files: {error}",)
        repository.update(job.job_id, destination=str(target))
        if in_failed and status is JobStatus.PUBLISHED:  # The old failed/ folder no longer means "failed".
            notes_from_cleanup = (_remove_old_failed_folder(folder, job),)
    if status is JobStatus.FAILED:
        try:
            write_error_report(target, job_id=job.job_id, stem=job.stem, status=status, errors=list(job.errors),
                               platform=job.platform)
        except OSError as error:
            logger.error("Could not write error report: job_id=%s folder=%s error=%s", job.job_id, target, error)
    return str(target), (f"files are in {target}", *notes_from_cleanup)


def _forget_picture(settings: Settings, repository: JobRepository, job: JobRecord, adapter) -> tuple[str, ...]:
    """Delete the picture the publisher stored for this job, then clear its key. A failure keeps the key."""
    if not job.media_host_key:
        return ()
    try:
        adapter.media_host(settings).delete(job.media_host_key)
    except Exception as error:  # Deliberately broad: the outcome is already recorded; the key stays for cleanup.
        logger.warning("Could not delete the stored picture; its key is kept: job_id=%s key=%s error=%s",
                       job.job_id, job.media_host_key, type(error).__name__)
        return (f"the stored picture could not be deleted ({type(error).__name__}); its key is kept for cleanup",)
    repository.clear_media_host_key(job.job_id)
    return ("the stored picture was deleted",)


def _remove_old_failed_folder(folder: Path, job: JobRecord) -> str:
    """After PUBLISHED moved the files out of failed/, remove the folder they left: it no longer means "failed".

    Only removed when it holds exactly what the app wrote there for this job (its marker, naming this job, and
    the error report); anything else, or a marker of another job, leaves it untouched and says so.
    """
    try:
        names = {child.name for child in folder.iterdir()}
        marker = (folder / JOB_MARKER_NAME).read_text(encoding="utf-8").strip() if JOB_MARKER_NAME in names else None
    except OSError as error:
        return f"the old folder {folder.name} in failed/ could not be read ({error}); it was left as it is"
    if marker != job.job_id or not names <= {JOB_MARKER_NAME, ERROR_REPORT_NAME}:
        return f"the old folder {folder.name} in failed/ holds other files; it was left as it is"
    try:
        for name in names:
            (folder / name).unlink()
        folder.rmdir()
    except OSError as error:
        return f"the old folder {folder.name} in failed/ could not be removed ({error})"
    return f"the old folder {folder.name} in failed/ was removed (it held only its error report)"
