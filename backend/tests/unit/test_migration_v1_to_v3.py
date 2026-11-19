"""A version-1 database (from before schedules) goes straight to version 3; the new job columns are NULL."""

import sqlite3
import uuid

from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.storage import database
from social_media_auto_poster.storage.database import open_app_db
from social_media_auto_poster.storage.jobs import COLUMNS, JobRepository


def _v1_database(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN; {database.MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
    repository = JobRepository(conn)  # Reads a version-1 table: the new columns are read as NULL.
    for index, status in enumerate((JobStatus.PUBLISHED, JobStatus.NEEDS_REVIEW, JobStatus.FAILED)):
        repository.create(job_id=str(uuid.uuid4()), platform=Platform.LINKEDIN, mode=PublishMode.REAL,
                          stem=f"post-{index}", media_name=f"post-{index}.jpg", caption_name=f"post-{index}.txt",
                          media_sha256=f"{index}" * 64, caption_sha256="c" * 64, status=status)
    conn.close()


def _query(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_a_v1_database_goes_to_v3_with_the_new_columns_null_and_every_row_kept(tmp_path):
    path = tmp_path / "app.db"
    _v1_database(path)
    before = _query(path, f"SELECT {', '.join(COLUMNS)} FROM jobs ORDER BY job_id")

    open_app_db(path).close()

    assert _query(path, "PRAGMA user_version") == [(3,)]
    assert sorted(p.name for p in tmp_path.iterdir() if ".bak-" in p.name) == ["app.db.bak-v1"]
    assert _query(path, f"SELECT {', '.join(COLUMNS)} FROM jobs ORDER BY job_id") == before
    columns = {row[1] for row in _query(path, "PRAGMA table_info(jobs)")}
    assert {"remote_ref", "media_host_key"} <= columns
    assert _query(path, "SELECT remote_ref, media_host_key FROM jobs") == [(None, None)] * 3
