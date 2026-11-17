"""Repository for scheduled posts (D46). All SQL for the ``schedules`` table lives here.

Every write is its own committed transaction. Only a ``SCHEDULED`` schedule can change: each
status change matches a row that is *still* ``SCHEDULED``, so a final schedule (``DONE``,
``INVALID``, ``CANCELLED``, ``MISSED``) is never touched again.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from social_media_auto_poster.models import Platform, PublishMode, ScheduleStatus
from social_media_auto_poster.storage.jobs import TIMESTAMP_FORMAT

# Explicit column order for SELECTs, so later migrations that add columns cannot shift fields.
COLUMNS = (
    "schedule_id", "platform", "mode", "stem", "media_name", "caption_name", "media_sha256", "caption_sha256",
    "publish_at", "status", "job_id", "reason", "created_at", "updated_at",
)
_SELECT = f"SELECT {', '.join(COLUMNS)} FROM schedules"


class DuplicateScheduleError(Exception):
    """The post already has an active (SCHEDULED) schedule on this platform."""


class ScheduleNotFoundError(Exception):
    """No SCHEDULED schedule with the given ID (unknown, or already final)."""


@dataclass(frozen=True)
class ScheduleRecord:
    schedule_id: str
    platform: Platform
    mode: PublishMode
    stem: str
    media_name: str
    caption_name: str
    media_sha256: str
    caption_sha256: str
    publish_at: datetime  # UTC.
    status: ScheduleStatus
    job_id: str | None
    reason: str | None
    created_at: datetime
    updated_at: datetime


class ScheduleRepository:
    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], datetime] | None = None):
        self._conn = conn
        self._now = now or (lambda: datetime.now(UTC))

    def create(
        self,
        *,
        schedule_id: str,
        platform: Platform,
        mode: PublishMode,
        stem: str,
        media_name: str,
        caption_name: str,
        media_sha256: str,
        caption_sha256: str,
        publish_at: datetime,
    ) -> ScheduleRecord:
        """Insert a SCHEDULED schedule. ``publish_at`` must carry a time zone; it is stored in UTC.

        Raises ``DuplicateScheduleError`` if the post (name compared without case) already has one.
        """
        if publish_at.tzinfo is None:
            raise ValueError("publish_at must carry a time zone")
        timestamp = self._timestamp()
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO schedules (schedule_id, platform, mode, stem, stem_key, media_name, caption_name,
                                           media_sha256, caption_sha256, publish_at, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        schedule_id,
                        platform.value,
                        mode.value,
                        stem,
                        stem.casefold(),
                        media_name,
                        caption_name,
                        media_sha256,
                        caption_sha256,
                        _format(publish_at),
                        ScheduleStatus.SCHEDULED.value,
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "schedules.platform, schedules.stem_key" in str(error):
                raise DuplicateScheduleError(f"'{stem}' already has an active schedule") from error
            raise
        return self._require(schedule_id)

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        row = self._conn.execute(f"{_SELECT} WHERE schedule_id = ?", (schedule_id,)).fetchone()
        return _to_record(row) if row else None

    def find_active(self, *, platform: Platform, stem: str) -> ScheduleRecord | None:
        """The SCHEDULED schedule of this post (name compared without case), if any."""
        row = self._conn.execute(
            f"{_SELECT} WHERE platform = ? AND stem_key = ? AND status = ?",
            (platform.value, stem.casefold(), ScheduleStatus.SCHEDULED.value),
        ).fetchone()
        return _to_record(row) if row else None

    def find_active_by_content(
        self, *, platform: Platform, mode: PublishMode, media_sha256: str, caption_sha256: str
    ) -> ScheduleRecord | None:
        """The SCHEDULED schedule (any post name) for exactly this content on this platform and mode, if any."""
        row = self._conn.execute(
            f"""
            {_SELECT}
            WHERE platform = ? AND mode = ? AND media_sha256 = ? AND caption_sha256 = ? AND status = ?
            ORDER BY publish_at, rowid LIMIT 1
            """,
            (platform.value, mode.value, media_sha256, caption_sha256, ScheduleStatus.SCHEDULED.value),
        ).fetchone()
        return _to_record(row) if row else None

    def list_active(self, *, platform: Platform | None = None) -> list[ScheduleRecord]:
        """Every SCHEDULED schedule (of one platform, if given), soonest ``publish_at`` first."""
        where, params = ("AND platform = ?", (platform.value,)) if platform is not None else ("", ())
        rows = self._conn.execute(
            f"{_SELECT} WHERE status = ? {where} ORDER BY publish_at, created_at, rowid",
            (ScheduleStatus.SCHEDULED.value, *params),
        ).fetchall()
        return [_to_record(row) for row in rows]

    def due(self, *, platform: Platform, mode: PublishMode, now: datetime) -> list[ScheduleRecord]:
        """SCHEDULED schedules of this platform and mode whose time has come, oldest ``publish_at`` first."""
        rows = self._conn.execute(
            f"""
            {_SELECT}
            WHERE platform = ? AND mode = ? AND status = ? AND publish_at <= ?
            ORDER BY publish_at, created_at, rowid
            """,
            (platform.value, mode.value, ScheduleStatus.SCHEDULED.value, _format(now)),
        ).fetchall()
        return [_to_record(row) for row in rows]

    def list_recent(self, limit: int = 50, *, status: ScheduleStatus | None = None) -> list[ScheduleRecord]:
        """The most recently created schedules (optionally only one status), newest first."""
        where, params = ("WHERE status = ?", (status.value,)) if status else ("", ())
        rows = self._conn.execute(
            f"{_SELECT} {where} ORDER BY created_at DESC, rowid DESC LIMIT ?", (*params, limit)
        ).fetchall()
        return [_to_record(row) for row in rows]

    def cancel(self, schedule_id: str) -> ScheduleRecord:
        return self._finish(schedule_id, ScheduleStatus.CANCELLED)

    def mark_done(self, schedule_id: str, *, job_id: str) -> ScheduleRecord:
        """The schedule was handed to job ``job_id`` (which must exist); that job's status is the outcome."""
        return self._finish(schedule_id, ScheduleStatus.DONE, job_id=job_id)

    def mark_invalid(self, schedule_id: str, *, reason: str) -> ScheduleRecord:
        return self._finish(schedule_id, ScheduleStatus.INVALID, reason=reason)

    def mark_missed(self, schedule_id: str, *, reason: str) -> ScheduleRecord:
        return self._finish(schedule_id, ScheduleStatus.MISSED, reason=reason)

    def _finish(
        self, schedule_id: str, status: ScheduleStatus, *, job_id: str | None = None, reason: str | None = None
    ) -> ScheduleRecord:
        """Move a still-SCHEDULED schedule to a final status. Raises ``ScheduleNotFoundError`` otherwise."""
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE schedules SET status = ?, job_id = ?, reason = ?, updated_at = ?
                WHERE schedule_id = ? AND status = ?
                """,
                (status.value, job_id, reason, self._timestamp(), schedule_id, ScheduleStatus.SCHEDULED.value),
            )
        if cursor.rowcount != 1:
            raise ScheduleNotFoundError(f"No scheduled post with ID {schedule_id}")
        return self._require(schedule_id)

    def _require(self, schedule_id: str) -> ScheduleRecord:
        record = self.get(schedule_id)
        if record is None:
            raise ScheduleNotFoundError(schedule_id)
        return record

    def _timestamp(self) -> str:
        return _format(self._now())


def schedules_available(conn: sqlite3.Connection) -> bool:
    """Whether this database has the schedules table (schema version 2 or later).

    Read-only callers (``scan``, ``schedules``) use this on a database that has not been migrated
    yet, instead of migrating it: an older database simply has no schedules.
    """
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schedules'").fetchone()
    return row is not None


def _format(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def _parse(text: str) -> datetime:
    return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=UTC)


def _to_record(row: tuple) -> ScheduleRecord:
    (
        schedule_id, platform, mode, stem, media_name, caption_name, media_sha256, caption_sha256,
        publish_at, status, job_id, reason, created_at, updated_at,
    ) = row
    return ScheduleRecord(
        schedule_id=schedule_id,
        platform=Platform(platform),
        mode=PublishMode(mode),
        stem=stem,
        media_name=media_name,
        caption_name=caption_name,
        media_sha256=media_sha256,
        caption_sha256=caption_sha256,
        publish_at=_parse(publish_at),
        status=ScheduleStatus(status),
        job_id=job_id,
        reason=reason,
        created_at=_parse(created_at),
        updated_at=_parse(updated_at),
    )
