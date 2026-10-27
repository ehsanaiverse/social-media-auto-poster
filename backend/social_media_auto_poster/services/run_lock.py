"""Make sure only one ``run`` processes the folders at a time.

The lock is an OS-level byte-range lock on an open file (``msvcrt.locking`` on
Windows, ``fcntl.flock`` elsewhere). The OS drops it when the file handle is
closed, including when the process crashes or is killed, so a stale lock file
on disk never blocks a later run; only a live process holding it does.
"""

import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class RunLockHeldError(Exception):
    """Another process currently holds the run lock."""


@contextmanager
def single_run_lock(path: Path) -> Generator[None, None, None]:
    """Hold an exclusive, non-blocking lock on ``path`` for the duration of the block.

    Raises ``RunLockHeldError`` immediately if another process holds it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        try:
            _lock(file)
        except OSError:
            raise RunLockHeldError(f"Lock is held by another process: {path}") from None
        try:
            yield
        finally:
            _unlock(file)


if sys.platform == "win32":
    import msvcrt

    def _lock(file: BinaryIO) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)  # Lock byte 0; fail at once if taken.

    def _unlock(file: BinaryIO) -> None:
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(file: BinaryIO) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(file: BinaryIO) -> None:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)
