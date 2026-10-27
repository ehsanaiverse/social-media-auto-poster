"""Test isolation shared by every test.

- Each test runs from its own empty temporary folder, which is also its base
  folder (``AUTOPOSTER_BASE_DIR``), with ``AUTOPOSTER_DATA_DIR`` unset. The
  application's default base folder is ``backend/`` whatever the current folder,
  so without this override a test could read or write the project's real
  ``.env``, ``data/``, ``database/``, ``logs/``, or lock file.
- A session-wide check fails the run if the real ``database/app.db`` changed
  (or was created) while the tests ran.
"""

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DB = REPO_ROOT / "database" / "app.db"


def _fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.fixture(autouse=True)
def _run_from_isolated_folder(tmp_path_factory, monkeypatch):
    cwd = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(cwd))
    monkeypatch.delenv("AUTOPOSTER_DATA_DIR", raising=False)


@pytest.fixture(autouse=True, scope="session")
def _real_database_is_never_touched():
    before = _fingerprint(REAL_DB)
    yield
    assert _fingerprint(REAL_DB) == before, f"A test changed or created the real job database: {REAL_DB}"
