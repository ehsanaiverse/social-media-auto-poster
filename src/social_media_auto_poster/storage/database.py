"""Open the application database (``database/app.db``) and keep its schema up to date.

This is the business-records store. It is separate from the LangGraph
checkpoint store (Milestone 3); never mix the two.
"""

import sqlite3
from pathlib import Path

from social_media_auto_poster.models import JobStatus

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
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply every migration newer than the database's ``user_version``, each in its own transaction."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"Database schema version {current} is newer than this application supports ({SCHEMA_VERSION}). "
            "Update the application; the database was not changed."
        )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        # executescript() manages no transaction of its own, so wrap each step explicitly:
        # a failed migration must leave both the schema and user_version unchanged.
        try:
            conn.executescript(f"BEGIN; {MIGRATIONS[version - 1]} PRAGMA user_version = {version}; COMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
