"""Content hashing for duplicate detection."""

import hashlib
from pathlib import Path

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB: the whole file is never held in memory.


def sha256_file(path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Return the lowercase hex SHA-256 of the file's bytes. Raises ``OSError`` if unreadable."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
