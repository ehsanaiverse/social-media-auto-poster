"""Schedule times and choosing what is due (D46). Pure functions: the caller passes "now".

Times are written as ``YYYY-MM-DD HH:MM`` in the configured zone (``Settings.timezone``,
default Asia/Karachi) and stored in UTC. Nothing here assumes the zone has no daylight saving
time: a local time that does not exist (clocks go forward) or happens twice (clocks go back)
is refused, never guessed.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import PostCandidate, PublishMode, ScheduleStatus
from social_media_auto_poster.platforms.linkedin.image_size import image_problem
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.storage.jobs import JobRepository
from social_media_auto_poster.storage.schedules import ScheduleRecord, ScheduleRepository

TIME_FORMAT = "%Y-%m-%d %H:%M"
TIME_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")  # ASCII digits only.
FORMAT_HINT = "YYYY-MM-DD HH:MM (24-hour clock, for example 2026-12-01 09:00)"

MIN_LEAD = timedelta(minutes=5)  # A schedule must be at least this far ahead.
MAX_LEAD = timedelta(days=365)  # ...and at most this far ahead.
GRACE_WINDOW = timedelta(hours=6)  # A due schedule is still published up to this late; later it is MISSED.
MAX_SCHEDULED_PER_RUN = 1  # Spaces posts by at least one scheduled-run interval.


class ScheduleTimeError(ValueError):
    """A schedule time that cannot be used; the message says why and how to fix it."""


class ScheduleError(Exception):
    """A schedule, unschedule or list request was refused; nothing was changed.

    ``kind`` is one of: not_found, pending, ambiguous, not_stable, unreadable, invalid, time, duplicate_job,
    duplicate_content, already_scheduled, not_scheduled, no_database.
    """

    def __init__(self, kind: str, message: str, details: tuple[str, ...] = ()):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details


def check_time_format(text: str) -> str:
    """``text`` stripped, if it has the ``YYYY-MM-DD HH:MM`` form and is a real date; else ``ScheduleTimeError``.

    Checks the form only (no zone, no clock), so the command line can refuse a malformed time as a usage error.
    """
    cleaned = text.strip()
    if not TIME_PATTERN.fullmatch(cleaned):
        raise ScheduleTimeError(f"Use the format {FORMAT_HINT}; got {text!r}.")
    try:
        datetime.strptime(cleaned, TIME_FORMAT)
    except ValueError:
        raise ScheduleTimeError(f"{text!r} is not a real date and time. Use the format {FORMAT_HINT}.") from None
    return cleaned


def parse_publish_at(text: str, *, zone: ZoneInfo, now: datetime) -> datetime:
    """``text`` (``YYYY-MM-DD HH:MM`` in ``zone``) as a UTC time. Raises ``ScheduleTimeError``."""
    _require_aware(now)
    cleaned = check_time_format(text)
    local = datetime.strptime(cleaned, TIME_FORMAT)

    zone_name = zone.key
    first, second = local.replace(tzinfo=zone, fold=0), local.replace(tzinfo=zone, fold=1)
    if not _exists(first):
        raise ScheduleTimeError(
            f"{cleaned} does not exist in {zone_name}: the clocks go forward over that time. Choose another time."
        )
    if first.utcoffset() != second.utcoffset():
        raise ScheduleTimeError(
            f"{cleaned} happens twice in {zone_name}: the clocks go back over that time. Choose another time."
        )

    publish_at = first.astimezone(UTC)
    if publish_at < now + MIN_LEAD:
        raise ScheduleTimeError(
            f"{cleaned} ({zone_name}) is too soon: a schedule must be at least 5 minutes ahead "
            f"(it is now {_local(now, zone)})."
        )
    if publish_at > now + MAX_LEAD:
        raise ScheduleTimeError(
            f"{cleaned} ({zone_name}) is too far away: a schedule must be at most 1 year ahead "
            f"(it is now {_local(now, zone)})."
        )
    return publish_at


def check_schedulable(
    candidate: PostCandidate, *, mode: PublishMode, jobs: JobRepository, schedules: ScheduleRepository
) -> tuple[str, str]:
    """Whether ``candidate`` may be scheduled in ``mode``; returns its (media, caption) SHA-256 hashes.

    The same checks a run would make, done now instead of at the scheduled time: validation (including
    the caption rule), LinkedIn's pixel limit for a real schedule, a live job with this content in this
    mode (duplicates are per mode, D20), and another active schedule for the same content. Raises
    ``ScheduleError``; the caller has already checked that both files are stable.
    """
    errors = validate_candidate(candidate).errors
    if errors:
        raise ScheduleError("invalid", f"'{candidate.stem}' is not valid: {'; '.join(errors)}.")
    try:
        if mode is PublishMode.REAL:
            problem = image_problem(candidate.media_path.read_bytes())
            if problem is not None:
                raise ScheduleError("invalid", f"'{candidate.stem}' cannot be posted to LinkedIn: {problem}.")
        media_sha256, caption_sha256 = sha256_file(candidate.media_path), sha256_file(candidate.caption_path)
    except OSError as error:
        raise ScheduleError("unreadable", f"the files of '{candidate.stem}' could not be read: {error}") from None

    job = jobs.find_blocking_duplicate(
        platform=candidate.platform, mode=mode, media_sha256=media_sha256, caption_sha256=caption_sha256
    )
    if job is not None:
        raise ScheduleError(
            "duplicate_job",
            f"'{candidate.stem}' is already handled by job {job.job_id[:8]} ({job.status}, {job.created_at:%Y-%m-%d}) "
            f"in {mode} mode; the same content is never published twice.",
        )
    other = schedules.find_active_by_content(
        platform=candidate.platform, mode=mode, media_sha256=media_sha256, caption_sha256=caption_sha256
    )
    if other is not None:
        raise ScheduleError(
            "duplicate_content",
            f"the same content is already scheduled as '{other.stem}' (schedule {other.schedule_id[:8]}).",
        )
    return media_sha256, caption_sha256


@dataclass(frozen=True)
class DueSchedule:
    schedule: ScheduleRecord
    late_by: timedelta  # How long after publish_at it is being published (zero if on time).


@dataclass(frozen=True)
class DueSelection:
    """What a scheduled run should do with the schedules whose time has come."""

    publish: tuple[DueSchedule, ...]  # At most MAX_SCHEDULED_PER_RUN: publish now.
    missed: tuple[ScheduleRecord, ...]  # More than GRACE_WINDOW late: mark MISSED, never publish.
    waiting: tuple[ScheduleRecord, ...]  # Due and in time, beyond this run's limit: left for the next runs.


def select_due(schedules: Iterable[ScheduleRecord], *, mode: PublishMode, now: datetime) -> DueSelection:
    """Split the SCHEDULED schedules of ``mode`` whose time has come, oldest ``publish_at`` first.

    Exactly ``GRACE_WINDOW`` late is still in time. Missed schedules never count toward the per-run limit.
    """
    _require_aware(now)
    due = sorted(
        (s for s in schedules if s.status is ScheduleStatus.SCHEDULED and s.mode is mode and s.publish_at <= now),
        key=lambda s: (s.publish_at, s.created_at),
    )
    missed = tuple(s for s in due if now - s.publish_at > GRACE_WINDOW)
    in_time = [s for s in due if now - s.publish_at <= GRACE_WINDOW]
    return DueSelection(
        publish=tuple(DueSchedule(s, now - s.publish_at) for s in in_time[:MAX_SCHEDULED_PER_RUN]),
        missed=missed,
        waiting=tuple(in_time[MAX_SCHEDULED_PER_RUN:]),
    )


def _exists(moment: datetime) -> bool:
    """Whether this wall-clock time really occurs in its zone (not skipped by a clock change)."""
    back = moment.astimezone(UTC).astimezone(moment.tzinfo)
    return back.replace(tzinfo=None, fold=0) == moment.replace(tzinfo=None, fold=0)


def _local(moment: datetime, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).strftime(TIME_FORMAT)


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must carry a time zone")
