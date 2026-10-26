"""Tests for opening and migrating the application database. Temporary files only."""

import re
import sqlite3

import pytest

from social_media_auto_poster.models import JobStatus
from social_media_auto_poster.storage import database
from social_media_auto_poster.storage.database import (
    BLOCKING_STATUSES,
    SCHEMA_VERSION,
    SchemaTooNewError,
    open_app_db,
)


def user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def table_sql(conn: sqlite3.Connection, name: str) -> str:
    return conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]


def test_creates_schema_and_parent_folder(tmp_path):
    path = tmp_path / "database" / "app.db"

    conn = open_app_db(path)

    assert path.exists()
    assert user_version(conn) == SCHEMA_VERSION == 1
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert {"jobs", "jobs_one_live_job_per_content", "jobs_by_status", "jobs_by_created"} <= names
    conn.close()


def test_reopening_does_not_migrate_again(tmp_path):
    path = tmp_path / "app.db"
    open_app_db(path).close()

    conn = open_app_db(path)

    assert user_version(conn) == 1
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
