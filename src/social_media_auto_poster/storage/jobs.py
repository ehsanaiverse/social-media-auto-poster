"""Repository for job records. All SQL for the ``jobs`` table lives here.

Every write is its own committed transaction, so a status written here is on
disk before the caller moves on (for example, before a publish call).
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.storage.database import BLOCKING_STATUSES

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Explicit column order for SELECTs, so later migrations that add columns cannot shift fields.
COLUMNS = (
    "job_id", "platform", "mode", "stem", "media_name", "caption_name", "media_sha256", "caption_sha256",
    "status", "platform_post_id", "destination", "errors", "created_at", "updated_at",
)
_SELECT = f"SELECT {', '.join(COLUMNS)} FROM jobs"


class DuplicateJobError(Exception):
    """A live job with the same content already exists (platform, mode, and both hashes)."""


class JobNotFoundError(Exception):
    """No job with the given ID."""


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    platform: Platform
    mode: PublishMode
    stem: str
    media_name: str
    caption_name: str
    media_sha256: str | None
    caption_sha256: str | None
    status: JobStatus
    platform_post_id: str | None
    destination: str | None
    errors: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


class _Unset:
    """Marks an ``update`` argument that was not passed (distinct from passing None)."""


_UNSET: Any = _Unset()


class JobRepository:
    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], datetime] | None = None):
        self._conn = conn
        self._now = now or (lambda: datetime.now(UTC))

    def create(
        self,
        *,
        job_id: str,
        platform: Platform,
        mode: PublishMode,
        stem: str,
        media_name: str,
        caption_name: str,
        media_sha256: str | None,
        caption_sha256: str | None,
        status: JobStatus,
        errors: tuple[str, ...] = (),
    ) -> JobRecord:
        """Insert a job. Raises ``DuplicateJobError`` if a live job has the same content."""
        timestamp = self._timestamp()
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO jobs (job_id, platform, mode, stem, media_name, caption_name,
                                      media_sha256, caption_sha256, status, errors, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        platform.value,
                        mode.value,
                        stem,
                        media_name,
                        caption_name,
                        media_sha256,
                        caption_sha256,
                        status.value,
                        json.dumps(list(errors)),
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "jobs.platform, jobs.mode, jobs.media_sha256, jobs.caption_sha256" in str(error):
                raise DuplicateJobError(f"A live job already exists for this content: {stem}") from error
            raise
        return self._require(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus = _UNSET,
        platform_post_id: str | None = _UNSET,
        destination: str | None = _UNSET,
        errors: tuple[str, ...] = _UNSET,
    ) -> JobRecord:
        """Change only the fields passed. Raises ``JobNotFoundError`` for an unknown ID."""
        changes: dict[str, object] = {}
        if status is not _UNSET:
            changes["status"] = status.value
        if platform_post_id is not _UNSET:
            changes["platform_post_id"] = platform_post_id
        if destination is not _UNSET:
            changes["destination"] = destination
        if errors is not _UNSET:
            changes["errors"] = json.dumps(list(errors))
        changes["updated_at"] = self._timestamp()

        # Column names come from the fixed keys above, never from callers.
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*changes.values(), job_id),
            )
        if cursor.rowcount == 0:
            raise JobNotFoundError(job_id)
        return self._require(job_id)

    def get(self, job_id: str) -> JobRecord | None:
        row = self._conn.execute(f"{_SELECT} WHERE job_id = ?", (job_id,)).fetchone()
        return self._to_record(row) if row else None

    def find_blocking_duplicate(
        self,
        *,
        platform: Platform,
        mode: PublishMode,
        media_sha256: str,
        caption_sha256: str,
    ) -> JobRecord | None:
        """The live job (if any) that already handles this exact content on this platform and mode."""
        placeholders = ", ".join("?" for _ in BLOCKING_STATUSES)
        row = self._conn.execute(
            f"""
            {_SELECT}
            WHERE platform = ? AND mode = ? AND media_sha256 = ? AND caption_sha256 = ?
              AND status IN ({placeholders})
            """,
            (platform.value, mode.value, media_sha256, caption_sha256, *(s.value for s in BLOCKING_STATUSES)),
        ).fetchone()
        return self._to_record(row) if row else None

    def _require(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        return record

    def _timestamp(self) -> str:
        return self._now().astimezone(UTC).strftime(TIMESTAMP_FORMAT)

    @staticmethod
    def _to_record(row: tuple) -> JobRecord:
        (
            job_id, platform, mode, stem, media_name, caption_name, media_sha256, caption_sha256,
            status, platform_post_id, destination, errors, created_at, updated_at,
        ) = row
        return JobRecord(
            job_id=job_id,
            platform=Platform(platform),
            mode=PublishMode(mode),
            stem=stem,
            media_name=media_name,
            caption_name=caption_name,
            media_sha256=media_sha256,
            caption_sha256=caption_sha256,
            status=JobStatus(status),
            platform_post_id=platform_post_id,
            destination=destination,
            errors=tuple(json.loads(errors)),
            created_at=_parse_timestamp(created_at),
            updated_at=_parse_timestamp(updated_at),
        )


def _parse_timestamp(text: str) -> datetime:
    return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
