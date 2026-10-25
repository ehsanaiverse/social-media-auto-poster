"""Decide whether a file has finished being copied into ``incoming/``."""

import time
from collections.abc import Callable
from pathlib import Path


def is_file_stable(
    path: Path,
    interval_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Return True if ``path`` is unchanged across ``interval_s`` and can be opened.

    Size and ``st_mtime_ns`` (integer nanoseconds, so no float rounding) must
    match before and after the wait. The file must then open for reading: on
    Windows a copy in progress usually holds a lock, so the open fails even if
    the copier set the final size up front.

    Any ``OSError`` (missing file, sharing violation) means "not stable yet"
    and returns False; the caller tries again on a later scan. A 0-byte file
    can be stable; validation rejects empty content.

    ``sleep`` is injectable so tests can change the file during the wait.
    """
    if interval_s < 0:
        raise ValueError("interval_s must not be negative.")
    try:
        before = path.stat()
        sleep(interval_s)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return False
        with path.open("rb"):
            pass
    except OSError:
        return False
    return True
