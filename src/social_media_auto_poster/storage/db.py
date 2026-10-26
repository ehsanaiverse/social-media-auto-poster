"""SQLite application database: schema and connection helper."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    stem TEXT NOT NULL,
    media_path TEXT NOT NULL,
    caption_path TEXT NOT NULL,
    media_hash TEXT,
    caption_hash TEXT,
    status TEXT NOT NULL,
    error TEXT,
    platform_post_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedupe
    ON jobs (platform, media_hash, caption_hash)
    WHERE media_hash IS NOT NULL AND caption_hash IS NOT NULL;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
