"""Tests for opening and migrating the application database. Temporary files only."""

import logging
import re
import sqlite3
import uuid
from datetime import UTC, datetime

import pytest

from social_media_auto_poster import main as cli
from social_media_auto_poster.models import JobStatus, Platform, PublishMode
from social_media_auto_poster.storage import database
from social_media_auto_poster.storage.jobs import DuplicateJobError, JobRepository
from social_media_auto_poster.storage.database import (
    BLOCKING_STATUSES,
    SCHEMA_VERSION,
    SchemaTooNewError,
    open_app_db,
    open_app_db_read_only,
)


def user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def table_sql(conn: sqlite3.Connection, name: str) -> str:
    return conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]


def test_creates_schema_and_parent_folder(tmp_path):
    path = tmp_path / "database" / "app.db"

    conn = open_app_db(path)

    assert path.exists()
    assert user_version(conn) == SCHEMA_VERSION == 3
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert {"jobs", "jobs_one_live_job_per_content", "jobs_by_status", "jobs_by_created"} <= names
    assert {"schedules", "schedules_one_active_per_post", "schedules_due"} <= names
    conn.close()


def test_reopening_does_not_migrate_again(tmp_path):
    path = tmp_path / "app.db"
    open_app_db(path).close()

    conn = open_app_db(path)

    assert user_version(conn) == 3
    conn.close()


def test_pragmas_are_set(tmp_path):
    conn = open_app_db(tmp_path / "app.db")

    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    conn.close()


def test_newer_schema_is_refused_and_left_unchanged(tmp_path):
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(SchemaTooNewError, match="99"):
        open_app_db(path)

    conn = sqlite3.connect(path)
    assert user_version(conn) == 99
    conn.close()


def test_failed_migration_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "MIGRATIONS", ("CREATE TABLE a (x); THIS IS NOT SQL;",))
    monkeypatch.setattr(database, "SCHEMA_VERSION", 1)
    path = tmp_path / "app.db"

    with pytest.raises(sqlite3.Error):
        open_app_db(path)

    conn = sqlite3.connect(path)
    assert user_version(conn) == 0
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'a'").fetchone()[0] == 0
    conn.close()


def test_status_check_matches_job_status_enum(tmp_path):
    conn = open_app_db(tmp_path / "app.db")

    sql = table_sql(conn, "jobs")
    allowed = re.search(r"status IN \(([^)]*)\)\)", sql).group(1)
    assert {s.strip(" '") for s in allowed.split(",")} == {s.value for s in JobStatus}
    conn.close()


def test_unique_index_covers_exactly_the_blocking_statuses(tmp_path):
    conn = open_app_db(tmp_path / "app.db")

    sql = table_sql(conn, "jobs_one_live_job_per_content")
    listed = re.search(r"status IN \(([^)]*)\)", sql).group(1)
    assert {s.strip(" '") for s in listed.split(",")} == {s.value for s in BLOCKING_STATUSES}
    assert JobStatus.FAILED not in BLOCKING_STATUSES
    conn.close()


def test_read_only_open_can_read_but_never_write(tmp_path):
    path = tmp_path / "app.db"
    open_app_db(path).close()

    conn = open_app_db_read_only(path)
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM jobs")
    conn.close()


def test_read_only_open_never_creates_a_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_app_db_read_only(tmp_path / "missing" / "app.db")
    assert not (tmp_path / "missing").exists()


def test_read_only_open_refuses_newer_schema(tmp_path):
    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(SchemaTooNewError):
        open_app_db_read_only(path)


def test_read_only_open_handles_spaces_in_path(tmp_path):
    path = tmp_path / "folder with spaces" / "app.db"
    open_app_db(path).close()

    open_app_db_read_only(path).close()


# Migration 2 (schedules, D46): an existing version-1 database is upgraded, and backed up first.

V1_BACKUP = "app.db.bak-v1"


def make_v1_database(path) -> list[str]:
    """A database exactly as version 1 left it, with three jobs in it. Returns their IDs."""
    conn = sqlite3.connect(path)
    conn.executescript(f"BEGIN; {database.MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
    repository = JobRepository(conn)
    job_ids = []
    for index, status in enumerate((JobStatus.PUBLISHED, JobStatus.SIMULATED, JobStatus.FAILED)):
        job_id = str(uuid.uuid4())
        mode = PublishMode.SIMULATED if status is JobStatus.SIMULATED else PublishMode.REAL
        repository.create(job_id=job_id, platform=Platform.LINKEDIN, mode=mode, stem=f"post-{index}",
                          media_name=f"post-{index}.jpg", caption_name=f"post-{index}.txt",
                          media_sha256=f"{index}" * 64, caption_sha256="c" * 64, status=status)
        job_ids.append(job_id)
    conn.close()
    return job_ids


def all_jobs(path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        from social_media_auto_poster.storage.jobs import COLUMNS

        return conn.execute(f"SELECT {', '.join(COLUMNS)} FROM jobs ORDER BY job_id").fetchall()
    finally:
        conn.close()


def backups(folder) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if ".bak-" in p.name)


def test_a_new_database_starts_at_version_2_without_a_backup(tmp_path):
    open_app_db(tmp_path / "app.db").close()

    assert backups(tmp_path) == []


def test_v1_database_migrates_to_v2_and_keeps_every_job_row(tmp_path):
    path = tmp_path / "app.db"
    make_v1_database(path)
    before = all_jobs(path)

    conn = open_app_db(path)

    assert user_version(conn) == 3
    assert conn.execute("SELECT count(*) FROM schedules").fetchone()[0] == 0
    conn.close()
    assert all_jobs(path) == before
    assert len(before) == 3


def test_duplicate_index_still_blocks_live_content_after_the_migration(tmp_path):
    path = tmp_path / "app.db"
    make_v1_database(path)
    conn = open_app_db(path)

    with pytest.raises(DuplicateJobError):
        JobRepository(conn).create(job_id=str(uuid.uuid4()), platform=Platform.LINKEDIN, mode=PublishMode.REAL,
                                   stem="again", media_name="again.jpg", caption_name="again.txt",
                                   media_sha256="0" * 64, caption_sha256="c" * 64, status=JobStatus.PUBLISHING)
    conn.close()


def test_migration_from_v1_writes_a_v1_backup_first(tmp_path):
    path = tmp_path / "app.db"
    make_v1_database(path)
    before = all_jobs(path)

    open_app_db(path).close()

    assert backups(tmp_path) == [V1_BACKUP]
    backup = sqlite3.connect(tmp_path / V1_BACKUP)
    assert user_version(backup) == 1
    assert backup.execute("SELECT count(*) FROM sqlite_master WHERE name = 'schedules'").fetchone()[0] == 0
    backup.close()
    assert all_jobs(tmp_path / V1_BACKUP) == before


def test_an_existing_backup_is_never_overwritten_and_a_timestamped_one_is_written(tmp_path, monkeypatch, caplog):
    path = tmp_path / "app.db"
    make_v1_database(path)
    (tmp_path / V1_BACKUP).write_bytes(b"an older backup the user keeps")
    monkeypatch.setattr(database, "_utc_now", lambda: datetime(2026, 11, 17, 12, 30, 0, tzinfo=UTC))

    with caplog.at_level(logging.INFO, logger="social_media_auto_poster.storage.database"):
        open_app_db(path).close()

    assert (tmp_path / V1_BACKUP).read_bytes() == b"an older backup the user keeps"
    timestamped = tmp_path / "app.db.bak-v1-20261117T123000Z"
    assert backups(tmp_path) == [V1_BACKUP, timestamped.name]
    assert all_jobs(timestamped) == all_jobs(path)
    assert str(timestamped) in caplog.text


def test_the_backup_path_is_logged(tmp_path, caplog):
    path = tmp_path / "app.db"
    make_v1_database(path)

    with caplog.at_level(logging.INFO, logger="social_media_auto_poster.storage.database"):
        open_app_db(path).close()

    assert str(tmp_path / V1_BACKUP) in caplog.text


def test_a_failed_backup_stops_the_migration_and_changes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    make_v1_database(path)

    def broken_link(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(database.os, "link", broken_link)
    with pytest.raises(OSError, match="disk full"):
        open_app_db(path)

    conn = sqlite3.connect(path)
    assert user_version(conn) == 1
    conn.close()
    assert [p.name for p in tmp_path.iterdir()] == ["app.db"]  # No backup and no temporary file left behind.


def test_an_up_to_date_database_is_not_backed_up_again(tmp_path):
    path = tmp_path / "app.db"
    make_v1_database(path)
    open_app_db(path).close()

    open_app_db(path).close()

    assert backups(tmp_path) == [V1_BACKUP]


def test_read_only_open_of_a_v1_database_neither_migrates_nor_backs_up(tmp_path):
    path = tmp_path / "app.db"
    make_v1_database(path)

    conn = open_app_db_read_only(path)
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 3
    conn.close()

    conn = sqlite3.connect(path)
    assert user_version(conn) == 1
    conn.close()
    assert backups(tmp_path) == []


def test_jobs_command_works_on_a_v1_database_without_migrating_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(tmp_path))
    (tmp_path / "database").mkdir()
    path = tmp_path / "database" / "app.db"
    make_v1_database(path)

    assert cli.main(["jobs"]) == 0

    assert "post-0" in capsys.readouterr().out
    conn = sqlite3.connect(path)
    assert user_version(conn) == 1
    conn.close()
    assert backups(tmp_path / "database") == []
