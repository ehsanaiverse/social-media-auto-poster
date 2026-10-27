"""The LangGraph checkpoint store (``database/langgraph_checkpoints.db``).

Graph execution state only, via the official ``SqliteSaver``. Business records
live in the application database (``storage/``); never read business data from
checkpoints or write business records here.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def open_checkpointer(path: Path, *, app_db_path: Path) -> Iterator[SqliteSaver]:
    """Open (creating if needed) the checkpoint store, and close it afterwards.

    Refuses the application database's path, so the two stores can never share a
    file. ``synchronous = FULL`` (with the WAL journal ``SqliteSaver`` sets up)
    means a saved checkpoint is on disk before the next graph step starts.
    """
    if path.resolve() == app_db_path.resolve():
        raise ValueError(f"The checkpoint store must not be the application database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False is what SqliteSaver documents: it serialises access with its own lock.
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")
        saver = SqliteSaver(conn)
        saver.setup()
        yield saver
    finally:
        conn.close()


def saved_thread_ids(saver: SqliteSaver) -> list[str]:
    """Every thread that still has a checkpoint, oldest first.

    Read straight from SqliteSaver's ``checkpoints`` table rather than through
    ``saver.list()``, which deserialises every checkpoint: one unreadable
    checkpoint must not hide the others.
    """
    with saver.cursor(transaction=False) as cur:
        cur.execute("SELECT thread_id FROM checkpoints GROUP BY thread_id ORDER BY MIN(rowid)")
        return [row[0] for row in cur.fetchall()]
