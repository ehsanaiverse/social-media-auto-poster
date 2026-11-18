"""The run log: handlers for one command or request, and the per-run ``run_id`` on every log-file line."""

import logging
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5

# The current run's short ID, added to every log-file line by _RunIdFilter. "-" outside a run.
_RUN_ID: ContextVar[str] = ContextVar("run_id", default="-")


@contextmanager
def logging_to(*, verbose: bool, log_file: Path | None, console: bool = True) -> Generator[None, None, None]:
    """Attach log handlers for one command or request: the terminal (``console``) and an optional rotating file.

    Handlers are removed and closed afterwards, so Windows never keeps the log
    file locked once the command returns. Log lines carry IDs, stems, platform,
    status, and error messages only, never caption text.
    """
    handlers: list[logging.Handler] = []
    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(logging.INFO if verbose else logging.WARNING)
        stream_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        handlers.append(stream_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        file_handler.addFilter(_RunIdFilter())
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s run_id=%(run_id)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%SZ"
        )
        formatter.converter = time.gmtime  # UTC, matching archive folder names and error reports.
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)
    for handler in handlers:
        root.addHandler(handler)
    try:
        yield
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)


class _RunIdFilter(logging.Filter):
    """Adds the current run's ID to every record (log file only; terminal lines stay short)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _RUN_ID.get()
        return True


@contextmanager
def _run_scope() -> Generator[str, None, None]:
    """Give everything logged inside the block (including graph nodes) a new short run ID."""
    token = _RUN_ID.set(uuid4().hex[:8])
    try:
        yield _RUN_ID.get()
    finally:
        _RUN_ID.reset(token)
