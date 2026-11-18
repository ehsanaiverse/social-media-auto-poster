"""Startup reconciliation: resolve jobs left PUBLISHING by a crash.

``run`` calls this after taking the single-run lock, so no other run can be
working on a PUBLISHING job: every PUBLISHING row found here is left over from
a process that crashed or was killed mid-publish.

- Simulated mode: nothing can have been sent, so the job becomes FAILED. FAILED
  never blocks, so the same run processes the post again if its files are still
  in incoming/.
- Real mode: the outcome is unknown, so the job becomes NEEDS_REVIEW and is never
  retried automatically. If its files are still in incoming/ with unchanged
  content, they move to failed/ with an error report telling the user to check
  the platform before re-posting.

The status is always written first, so even if moving files fails, the row keeps
blocking a re-publish.

With checkpoints (Milestone 3), startup resume runs first. When a job's saved
checkpoint proves publish never started, it settles the row with
``settle_crashed_before_publish`` (FAILED in both modes). Jobs whose resume failed
are passed in ``skip`` and left for the next run's resume.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from social_media_auto_poster.config import Settings
from social_media_auto_poster.models import JobStatus, PostCandidate, PublishMode
from social_media_auto_poster.services.archive import ArchiveError, archive_post, post_folder_name, write_error_report
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository

logger = logging.getLogger(__name__)

SIMULATED_INTERRUPTED = "simulated publish was interrupted by a crash; nothing was sent"
REAL_INTERRUPTED = "publish was interrupted by a crash; the outcome is unknown - check the platform before re-posting"
BEFORE_PUBLISH_INTERRUPTED = "interrupted by a crash before publishing; nothing was sent"


@dataclass(frozen=True)
class Recovery:
    """What reconciliation did to one interrupted job."""

    job_id: str
    stem: str
    mode: PublishMode
    status: JobStatus  # The new status: FAILED (simulated) or NEEDS_REVIEW (real).
    # Human-readable, shown by run. Real recoveries store every note in the job's errors;
    # simulated ones store only the first, as the follow-up describes this run, not the job.
    notes: tuple[str, ...]
    destination: str | None = None  # failed/ folder, if the files were moved.


def reconcile_interrupted_jobs(
    repository: JobRepository, settings: Settings, *, skip: frozenset[str] = frozenset()
) -> list[Recovery]:
    """Resolve every PUBLISHING job except those in ``skip``. Database errors propagate; file errors are recorded."""
    recoveries = []
    for record in repository.list_by_status(JobStatus.PUBLISHING):
        if record.job_id in skip:
            continue
        if record.mode is PublishMode.SIMULATED:
            recovery = _recover_simulated(record, repository, settings)
        else:
            recovery = _recover_real(record, repository, settings)
        logger.warning(
            "Recovered interrupted job: job_id=%s platform=%s stem=%s mode=%s status=%s notes=%s",
            record.job_id,
            record.platform,
            record.stem,
            record.mode,
            recovery.status,
            list(recovery.notes),
        )
        recoveries.append(recovery)
    return recoveries


def settle_crashed_before_publish(record: JobRecord, repository: JobRepository, settings: Settings) -> Recovery:
    """A PUBLISHING job whose checkpoint proves publish never started: FAILED in either mode.

    FAILED never blocks, so the same run processes the post again if its files are
    still in incoming/.
    """
    recovery = _mark_failed(record, repository, settings, BEFORE_PUBLISH_INTERRUPTED)
    logger.warning(
        "Recovered interrupted job: job_id=%s platform=%s stem=%s mode=%s status=%s notes=%s",
        record.job_id,
        record.platform,
        record.stem,
        record.mode,
        recovery.status,
        list(recovery.notes),
    )
    return recovery


def _recover_simulated(record: JobRecord, repository: JobRepository, settings: Settings) -> Recovery:
    return _mark_failed(record, repository, settings, SIMULATED_INTERRUPTED)


def _mark_failed(record: JobRecord, repository: JobRepository, settings: Settings, reason: str) -> Recovery:
    candidate = _candidate(record, settings)
    in_incoming = candidate.media_path.is_file() and candidate.caption_path.is_file()
    follow_up = "will be processed again now" if in_incoming else "its files are no longer in incoming/"
    repository.update(record.job_id, status=JobStatus.FAILED, errors=(*record.errors, reason))
    return Recovery(record.job_id, record.stem, record.mode, JobStatus.FAILED, (reason, follow_up))


def _recover_real(record: JobRecord, repository: JobRepository, settings: Settings) -> Recovery:
    # Status first: from here on the row blocks a re-publish whatever happens to the files.
    errors = [*record.errors, REAL_INTERRUPTED]
    repository.update(record.job_id, status=JobStatus.NEEDS_REVIEW, errors=tuple(errors))

    candidate = _candidate(record, settings)
    notes = [REAL_INTERRUPTED]
    destination = None
    file_note = _files_safe_to_move(record, candidate)
    if file_note is not None:
        notes.append(file_note)
    else:
        try:
            folder = archive_post(
                candidate,
                settings.failed_dir(candidate.platform),
                job_id=record.job_id,
                folder_name=post_folder_name(record.stem, datetime.now(UTC)),
            )
        except (OSError, ArchiveError) as error:
            notes.append(f"could not move files to failed/: {error}")
        else:
            destination = str(folder)
            notes.append(f"files moved to {folder.name}")
            try:
                write_error_report(
                    folder, job_id=record.job_id, stem=record.stem, status=JobStatus.NEEDS_REVIEW, errors=errors
                )
            except OSError as error:
                logger.error("Could not write error report: job_id=%s folder=%s error=%s", record.job_id, folder, error)

    repository.update(record.job_id, errors=(*errors, *notes[1:]), destination=destination)
    return Recovery(record.job_id, record.stem, record.mode, JobStatus.NEEDS_REVIEW, tuple(notes), destination)


def _files_safe_to_move(record: JobRecord, candidate: PostCandidate) -> str | None:
    """None if both files are in incoming/ with the recorded content; otherwise why they stay put."""
    if not (candidate.media_path.is_file() and candidate.caption_path.is_file()):
        return "files are not in incoming/; nothing was moved"
    try:
        same = (
            sha256_file(candidate.media_path) == record.media_sha256
            and sha256_file(candidate.caption_path) == record.caption_sha256
        )
    except OSError as error:
        return f"could not read files to verify them ({error}); left in incoming/"
    if not same:
        return "files in incoming/ now have different content; left in place"
    return None


def _candidate(record: JobRecord, settings: Settings) -> PostCandidate:
    incoming = settings.incoming_dir(record.platform)
    return PostCandidate(record.platform, record.stem, incoming / record.media_name, incoming / record.caption_name)
