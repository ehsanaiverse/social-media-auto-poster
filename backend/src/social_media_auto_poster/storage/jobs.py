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
# Added by migration 3. A database opened read-only at an older version has no such columns; they read as NULL.
REFERENCE_COLUMNS = ("remote_ref", "media_host_key")


class DuplicateJobError(Exception):
    """A live job with the same content already exists (platform, mode, and both hashes)."""


class JobNotFoundError(Exception):
    """No job with the given ID."""


class ReferenceNotRecordedError(Exception):
    """The job is not PUBLISHING, or already has a reference: nothing was written."""


class ScheduleNotSettledError(Exception):
    """The schedule a new job belongs to is no longer SCHEDULED; the job was not created (nothing changed)."""


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
    remote_ref: str | None = None  # The platform's pre-publish reference (Instagram: container ID).
    media_host_key: str | None = None  # The media host object to clean up, if any.


class _Unset:
    """Marks an ``update`` argument that was not passed (distinct from passing None)."""


_UNSET: Any = _Unset()


class JobRepository:
    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], datetime] | None = None):
        self._conn = conn
        self._now = now or (lambda: datetime.now(UTC))
        present = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        references = [name if name in present else "NULL" for name in REFERENCE_COLUMNS]
        self._select = f"SELECT {', '.join((*COLUMNS, *references))} FROM jobs"

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
        settle_schedule: str | None = None,
    ) -> JobRecord:
        """Insert a job. Raises ``DuplicateJobError`` if a live job has the same content.

        With ``settle_schedule`` (D46), the same transaction also marks that schedule DONE with this job's
        ID. Both are written or neither: if the schedule is no longer SCHEDULED, the insert is rolled back
        and ``ScheduleNotSettledError`` is raised. This is how a scheduled post can be handed to exactly
        one job, even if the process dies right after.
        """
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
                if settle_schedule is not None:
                    settled = self._conn.execute(
                        """
                        UPDATE schedules SET status = 'DONE', job_id = ?, updated_at = ?
                        WHERE schedule_id = ? AND status = 'SCHEDULED'
                        """,
                        (job_id, timestamp, settle_schedule),
                    )
                    if settled.rowcount != 1:  # Raised inside the transaction: the insert is rolled back too.
                        raise ScheduleNotSettledError(f"schedule {settle_schedule} is no longer SCHEDULED")
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

    def resolve_needs_review(
        self,
        job_id: str,
        *,
        status: JobStatus,
        note: str,
        platform_post_id: str | None = None,
    ) -> JobRecord:
        """Manually resolve a NEEDS_REVIEW job as PUBLISHED or FAILED, appending ``note`` to its errors.

        The UPDATE only matches a row that is *still* NEEDS_REVIEW, so no other
        row (or a row whose status changed meanwhile) can ever be modified.
        Raises ``JobNotFoundError`` if no NEEDS_REVIEW job has this ID.
        """
        if status not in (JobStatus.PUBLISHED, JobStatus.FAILED):
            raise ValueError("A NEEDS_REVIEW job can only be resolved as PUBLISHED or FAILED.")
        current = self.get(job_id)
        if current is None or current.status is not JobStatus.NEEDS_REVIEW:
            raise JobNotFoundError(f"No NEEDS_REVIEW job with ID {job_id}")
        errors = json.dumps([*current.errors, note])
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE jobs SET status = ?, platform_post_id = ?, errors = ?, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (status.value, platform_post_id, errors, self._timestamp(), job_id, JobStatus.NEEDS_REVIEW.value),
            )
        if cursor.rowcount != 1:
            raise JobNotFoundError(f"No NEEDS_REVIEW job with ID {job_id}")
        return self._require(job_id)

    def record_remote_ref(self, job_id: str, reference: str) -> None:
        """Store the platform's pre-publish reference in its own committed transaction (D1).

        Called before the publish call (Instagram: before media_publish), so a job with no reference
        was never published. Only a PUBLISHING job without a reference is changed; anything else raises
        ``ReferenceNotRecordedError`` and writes nothing. Database errors propagate.
        """
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE jobs SET remote_ref = ?, updated_at = ? "
                "WHERE job_id = ? AND status = ? AND remote_ref IS NULL",
                (reference, self._timestamp(), job_id, JobStatus.PUBLISHING.value),
            )
            if cursor.rowcount != 1:
                raise ReferenceNotRecordedError(f"job {job_id} is not PUBLISHING without a reference")

    def get(self, job_id: str) -> JobRecord | None:
        row = self._conn.execute(f"{self._select} WHERE job_id = ?", (job_id,)).fetchone()
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
            {self._select}
            WHERE platform = ? AND mode = ? AND media_sha256 = ? AND caption_sha256 = ?
              AND status IN ({placeholders})
            """,
            (platform.value, mode.value, media_sha256, caption_sha256, *(s.value for s in BLOCKING_STATUSES)),
        ).fetchone()
        return self._to_record(row) if row else None

    def has_jobs(self, *, mode: PublishMode, platform: Platform = Platform.LINKEDIN) -> bool:
        """Whether any job (in any status) was ever recorded for ``platform`` in ``mode``.

        Per platform, so one platform's real history never stands in for another's (D38, D49). Production
        code always passes ``platform``; the LinkedIn default only keeps an older test that omits it working.
        """
        row = self._conn.execute(
            "SELECT 1 FROM jobs WHERE platform = ? AND mode = ? LIMIT 1", (platform.value, mode.value)
        ).fetchone()
        return row is not None

    def list_by_status(self, status: JobStatus) -> list[JobRecord]:
        """All jobs with ``status``, oldest first."""
        rows = self._conn.execute(f"{self._select} WHERE status = ? ORDER BY created_at, rowid", (status.value,)).fetchall()
        return [self._to_record(row) for row in rows]

    def list_recent(self, limit: int = 20, *, status: JobStatus | None = None) -> list[JobRecord]:
        """The most recently created jobs (optionally only one status), newest first."""
        where, params = ("WHERE status = ?", (status.value,)) if status else ("", ())
        rows = self._conn.execute(
            f"{self._select} {where} ORDER BY created_at DESC, rowid DESC LIMIT ?", (*params, limit)
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def find_by_id_prefix(self, prefix: str) -> list[JobRecord]:
        """Jobs whose ID starts with ``prefix`` (compared exactly, no wildcards), newest first."""
        rows = self._conn.execute(
            f"{self._select} WHERE substr(job_id, 1, ?) = ? ORDER BY created_at DESC, rowid DESC",
            (len(prefix), prefix),
        ).fetchall()
        return [self._to_record(row) for row in rows]

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
            status, platform_post_id, destination, errors, created_at, updated_at, remote_ref, media_host_key,
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
            remote_ref=remote_ref,
            media_host_key=media_host_key,
        )


def _parse_timestamp(text: str) -> datetime:
    return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
