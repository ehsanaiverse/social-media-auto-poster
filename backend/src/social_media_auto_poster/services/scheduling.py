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

from social_media_auto_poster.models import PublishMode, ScheduleStatus
from social_media_auto_poster.storage.schedules import ScheduleRecord

TIME_FORMAT = "%Y-%m-%d %H:%M"
TIME_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")  # ASCII digits only.
FORMAT_HINT = "YYYY-MM-DD HH:MM (24-hour clock, for example 2026-12-01 09:00)"

MIN_LEAD = timedelta(minutes=5)  # A schedule must be at least this far ahead.
MAX_LEAD = timedelta(days=365)  # ...and at most this far ahead.
GRACE_WINDOW = timedelta(hours=6)  # A due schedule is still published up to this late; later it is MISSED.
MAX_SCHEDULED_PER_RUN = 1  # Spaces posts by at least one scheduled-run interval.


class ScheduleTimeError(ValueError):
    """A schedule time that cannot be used; the message says why and how to fix it."""


def parse_publish_at(text: str, *, zone: ZoneInfo, now: datetime) -> datetime:
    """``text`` (``YYYY-MM-DD HH:MM`` in ``zone``) as a UTC time. Raises ``ScheduleTimeError``."""
    _require_aware(now)
    cleaned = text.strip()
    if not TIME_PATTERN.fullmatch(cleaned):
        raise ScheduleTimeError(f"Use the format {FORMAT_HINT}; got {text!r}.")
    try:
        local = datetime.strptime(cleaned, TIME_FORMAT)
    except ValueError:
        raise ScheduleTimeError(f"{text!r} is not a real date and time. Use the format {FORMAT_HINT}.") from None

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
