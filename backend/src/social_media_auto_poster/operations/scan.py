"""The read-only scan (``scan`` and the API's scan): what the next run would do with each post. Never writes.

Also the two checks every run and schedule shares: whether a post's files are still changing
(``unstable_files``) and whether its content is already handled (``find_duplicate``).
"""

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from social_media_auto_poster.config import Settings
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.ingestion.stability import is_file_stable
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, PostCandidate, PublishMode, ScanResult
from social_media_auto_poster.operations.common import LOGGER_NAME, PLATFORM
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.real_mode import archive_without_history, posts_only_simulated
from social_media_auto_poster.services.scheduling import content_state
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository
from social_media_auto_poster.storage.schedules import ScheduleRecord, ScheduleRepository, schedules_available

logger = logging.getLogger(LOGGER_NAME)  # Same name as before the move, so log lines are unchanged.


@dataclass(frozen=True)
class CandidateView:
    """What a read-only scan knows about one candidate post."""

    candidate: PostCandidate
    unstable: tuple[str, ...]  # "media" and/or "caption" while still changing or locked.
    errors: tuple[str, ...]  # Validation errors; empty when valid.
    duplicate: JobRecord | None  # A live job with the same content in this mode.
    duplicate_check_failed: bool  # The files could not be read to check.
    simulated: JobRecord | None  # Real mode only: this content was simulated and a real run refuses it.
    schedule: ScheduleRecord | None = None  # An active schedule: runs skip the post until it is due.
    schedule_content: str | None = None  # With a schedule: ok, changed, missing or unreadable (content_state).

    @property
    def stable(self) -> bool:
        return not self.unstable

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ScanPreview:
    folder: Path
    candidates: tuple[CandidateView, ...]
    scan: ScanResult
    interrupted_jobs: int  # Jobs in PUBLISHING: a crash left them, and the next run recovers them.
    unknown_archive: tuple[Path, ...]  # Real mode only: archive folders the job database cannot account for.
    # Active schedules whose post is no longer a complete pair in the folder: they will not be published.
    missing_schedules: tuple["ScheduleView", ...] = ()


@dataclass(frozen=True)
class ScheduleView:
    """A schedule and, for an active one, whether its files still match what was confirmed (read-only)."""

    schedule: ScheduleRecord
    content: str | None  # ok, changed, missing or unreadable for SCHEDULED; None for finished schedules.


def unstable_files(candidate: PostCandidate, interval_s: float) -> tuple[str, ...]:
    """Which of the two files ("media", "caption") is still changing or locked. Both must be complete to post."""
    files = {"media": candidate.media_path, "caption": candidate.caption_path}
    return tuple(role for role, path in files.items() if not is_file_stable(path, interval_s))


def find_duplicate(candidate: PostCandidate, repository: JobRepository, mode: PublishMode) -> JobRecord | None:
    """The live job that already handles this content in ``mode``, if any. Raises OSError if a file cannot be read."""
    media_sha256 = sha256_file(candidate.media_path)
    caption_sha256 = sha256_file(candidate.caption_path)
    return repository.find_blocking_duplicate(
        platform=candidate.platform, mode=mode, media_sha256=media_sha256, caption_sha256=caption_sha256
    )


def scan_preview(
    settings: Settings, *, folder: Path, mode: PublishMode, interval_s: float, check_archive: bool
) -> ScanPreview:
    """Read-only report of ``folder``. Never moves, publishes, or creates anything.

    Raises FileNotFoundError if the folder does not exist. ``check_archive`` adds the
    real-mode archive check (only meaningful for the configured incoming folder).
    """
    result = scan_incoming(folder, PLATFORM)
    conn = _open_history_or_none(settings)
    try:
        repository = JobRepository(conn) if conn is not None else None
        # Schedules belong to the configured incoming folder. An older database (no schedules table) is
        # read as it is, never upgraded: it has no schedules.
        own_folder = folder.resolve() == settings.incoming_dir(PLATFORM).resolve()
        active = (_active_schedules(ScheduleRepository(conn))
                  if own_folder and conn is not None and schedules_available(conn) else {})
        views = tuple(
            _view_candidate(c, repository, mode, interval_s, active.get(c.stem.casefold()), folder)
            for c in result.candidates
        )
        present = {c.stem.casefold() for c in result.candidates}
        missing = tuple(ScheduleView(s, content_state(s, folder)) for key, s in active.items() if key not in present)
        interrupted = len(repository.list_by_status(JobStatus.PUBLISHING)) if repository is not None else 0
        unknown: tuple[Path, ...] = ()
        if mode is PublishMode.REAL and check_archive:
            unknown = tuple(archive_without_history(settings, repository, PLATFORM))
    finally:
        if conn is not None:
            conn.close()
    return ScanPreview(folder=folder, candidates=views, scan=result, interrupted_jobs=interrupted,
                       unknown_archive=unknown, missing_schedules=missing)


def _active_schedules(schedules: ScheduleRepository) -> dict[str, ScheduleRecord]:
    """Active schedules of this platform by casefolded post name (names are compared without case)."""
    return {s.stem.casefold(): s for s in schedules.list_active(platform=PLATFORM)}


def _view_candidate(
    candidate: PostCandidate,
    repository: JobRepository | None,
    mode: PublishMode,
    interval_s: float,
    schedule: ScheduleRecord | None = None,
    folder: Path | None = None,
) -> CandidateView:
    unstable = unstable_files(candidate, interval_s)
    errors = tuple(validate_candidate(candidate).errors)
    duplicate = None
    failed = False
    simulated = None
    # Only a stable, valid post is checked for duplicates: a file still changing is not hashed.
    if repository is not None and not unstable and not errors:
        try:
            duplicate = find_duplicate(candidate, repository, mode)
        except OSError:
            failed = True
        if duplicate is None and not failed and mode is PublishMode.REAL:
            found = posts_only_simulated((candidate,), repository)
            simulated = found[0].simulated_job if found else None
    content = content_state(schedule, folder or candidate.media_path.parent) if schedule is not None else None
    return CandidateView(candidate, unstable, errors, duplicate, failed, simulated, schedule, content)


def _open_history_or_none(settings: Settings) -> sqlite3.Connection | None:
    """The job database, read-only, or None if it does not exist yet or cannot be read (scan still works)."""
    try:
        return open_app_db_read_only(settings.app_db_path)
    except FileNotFoundError:
        return None
    except (SchemaTooNewError, sqlite3.Error) as error:
        logger.warning("Duplicate check unavailable; could not read the job database: %s", error)
        return None
