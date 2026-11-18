"""Open the application database (``database/app.db``) and keep its schema up to date.

This is the business-records store. It is separate from the LangGraph
checkpoint store (Milestone 3); never mix the two.
"""

import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from social_media_auto_poster.models import JobStatus

logger = logging.getLogger(__name__)

# Statuses that mean "this content is already handled": a second job with the
# same content (per platform and mode) must not be created while one exists.
# FAILED is deliberately absent, so a fixed post can be retried.
BLOCKING_STATUSES = (
    JobStatus.PUBLISHING,
    JobStatus.PUBLISHED,
    JobStatus.NEEDS_REVIEW,
    JobStatus.SIMULATED,
)


def _sql_list(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


# Each entry upgrades the schema by one version. Never edit an entry once it
# has shipped; append a new one instead.
MIGRATIONS: tuple[str, ...] = (
    f"""
    CREATE TABLE jobs (
        job_id           TEXT PRIMARY KEY,
        platform         TEXT NOT NULL,
        mode             TEXT NOT NULL CHECK (mode IN ('simulated', 'real')),
        stem             TEXT NOT NULL,
        media_name       TEXT NOT NULL,
        caption_name     TEXT NOT NULL,
        media_sha256     TEXT,
        caption_sha256   TEXT,
        status           TEXT NOT NULL CHECK (status IN ({_sql_list(JobStatus)})),
        platform_post_id TEXT,
        destination      TEXT,
        errors           TEXT NOT NULL DEFAULT '[]',
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        CHECK (status <> 'PUBLISHED' OR mode = 'real'),
        CHECK (status <> 'SIMULATED' OR mode = 'simulated'),
        CHECK (status NOT IN ({_sql_list(BLOCKING_STATUSES)})
               OR (media_sha256 IS NOT NULL AND caption_sha256 IS NOT NULL))
    );

    CREATE UNIQUE INDEX jobs_one_live_job_per_content
        ON jobs (platform, mode, media_sha256, caption_sha256)
        WHERE status IN ({_sql_list(BLOCKING_STATUSES)});

    CREATE INDEX jobs_by_status ON jobs (status);
    CREATE INDEX jobs_by_created ON jobs (created_at);
    """,
    # 2: scheduled posts (D46). Values are written out, not taken from the enums, so this text never changes.
    # stem_key is the post name casefolded in Python (SQLite's lower() only folds ASCII).
    """
    CREATE TABLE schedules (
        schedule_id    TEXT PRIMARY KEY,
        platform       TEXT NOT NULL,
        mode           TEXT NOT NULL CHECK (mode IN ('simulated', 'real')),
        stem           TEXT NOT NULL,
        stem_key       TEXT NOT NULL,
        media_name     TEXT NOT NULL,
        caption_name   TEXT NOT NULL,
        media_sha256   TEXT NOT NULL,
        caption_sha256 TEXT NOT NULL,
        publish_at     TEXT NOT NULL,
        status         TEXT NOT NULL CHECK (status IN ('SCHEDULED', 'DONE', 'INVALID', 'CANCELLED', 'MISSED')),
        job_id         TEXT REFERENCES jobs (job_id),
        reason         TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        CHECK (status <> 'DONE' OR job_id IS NOT NULL)
    );

    CREATE UNIQUE INDEX schedules_one_active_per_post
        ON schedules (platform, stem_key)
        WHERE status = 'SCHEDULED';

    CREATE INDEX schedules_due ON schedules (status, publish_at);
    """,
)

SCHEMA_VERSION = len(MIGRATIONS)


class SchemaTooNewError(Exception):
    """The database was created by a newer version of this application."""


def open_app_db(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the application database and apply pending migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")  # A commit is on disk before we publish.
        conn.execute("PRAGMA busy_timeout = 5000")
        migrate(conn, db_path=path)
    except BaseException:
        conn.close()
        raise
    return conn


def open_app_db_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing application database without any ability to change it.

    Raises ``FileNotFoundError`` if it does not exist (it is never created here)
    and ``SchemaTooNewError`` if it was written by a newer application.
    """
    if not path.is_file():
        raise FileNotFoundError(f"No job database at {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"Database schema version {current} is newer than this application supports ({SCHEMA_VERSION})."
            )
    except BaseException:
        conn.close()
        raise
    return conn


def migrate(conn: sqlite3.Connection, *, db_path: Path | None = None) -> None:
    """Apply every migration newer than the database's ``user_version``, each in its own transaction.

    With ``db_path``, an existing database (version 1 or later) is first copied next to itself
    (``backup_before_migration``); if that copy fails, nothing is migrated. A new database has
    nothing to keep, so it is not copied.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"Database schema version {current} is newer than this application supports ({SCHEMA_VERSION}). "
            "Update the application; the database was not changed."
        )
    if db_path is not None and 1 <= current < SCHEMA_VERSION:
        backup_before_migration(conn, db_path, current)
    for version in range(current + 1, SCHEMA_VERSION + 1):
        # executescript() manages no transaction of its own, so wrap each step explicitly:
        # a failed migration must leave both the schema and user_version unchanged.
        try:
            conn.executescript(f"BEGIN; {MIGRATIONS[version - 1]} PRAGMA user_version = {version}; COMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise


def backup_before_migration(conn: sqlite3.Connection, db_path: Path, version: int) -> Path:
    """Copy the database to ``<name>.bak-v<version>`` next to it before its schema changes (D46).

    An existing file is never replaced: if that name is taken, the copy gets the current UTC time
    (``app.db.bak-v1-20261117T123000Z``). The copy uses SQLite's backup API, is written under a
    temporary name first, and only then given its final name, so a half-written backup never
    carries a backup's name. The path is logged.
    """
    base = f"{db_path.name}.bak-v{version}"
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    names = [base, f"{base}-{stamp}", *(f"{base}-{stamp}-{n}" for n in range(2, 100))]
    temporary = db_path.with_name(f"{db_path.name}.bak-tmp-{uuid.uuid4().hex}")
    try:
        target = sqlite3.connect(temporary)
        try:
            conn.backup(target)
        finally:
            target.close()
        for name in names:
            backup = db_path.with_name(name)
            try:
                os.link(temporary, backup)  # Fails instead of replacing an existing file.
            except FileExistsError:
                continue
            logger.warning(
                "Job database schema upgrade from version %d to %d: the old database was copied to %s",
                version, SCHEMA_VERSION, backup,
            )
            return backup
        raise FileExistsError(f"No free backup name next to {db_path}; the database was not changed.")
    finally:
        temporary.unlink(missing_ok=True)


def _utc_now() -> datetime:
    return datetime.now(UTC)
