"""Repository layer for application job records."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from social_media_auto_poster.models import JobStatus, Platform


@dataclass
class JobRecord:
    id: int
    platform: Platform
    stem: str
    media_path: Path
    caption_path: Path
    media_hash: str | None
    caption_hash: str | None
    status: JobStatus
    error: str | None
    platform_post_id: str | None


class JobRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(self, platform: Platform, stem: str, media_path: Path, caption_path: Path,
               media_hash: str | None = None, caption_hash: str | None = None,
               status: JobStatus = JobStatus.DISCOVERED) -> int:
        cur = self._conn.execute(
            "INSERT INTO jobs (platform, stem, media_path, caption_path, media_hash, caption_hash, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (platform.value, stem, str(media_path), str(caption_path), media_hash, caption_hash, status.value),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get(self, job_id: int) -> JobRecord | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def find_by_hashes(self, platform: Platform, media_hash: str, caption_hash: str) -> JobRecord | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE platform=? AND media_hash=? AND caption_hash=?",
            (platform.value, media_hash, caption_hash),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def update_status(self, job_id: int, status: JobStatus, error: str | None = None,
                      platform_post_id: str | None = None) -> None:
        self._conn.execute(
            "UPDATE jobs SET status=?, error=?, platform_post_id=?, updated_at=datetime('now') WHERE id=?",
            (status.value, error, platform_post_id, job_id),
        )
        self._conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            platform=Platform(row["platform"]),
            stem=row["stem"],
            media_path=Path(row["media_path"]),
            caption_path=Path(row["caption_path"]),
            media_hash=row["media_hash"],
            caption_hash=row["caption_hash"],
            status=JobStatus(row["status"]),
            error=row["error"],
            platform_post_id=row["platform_post_id"],
        )
