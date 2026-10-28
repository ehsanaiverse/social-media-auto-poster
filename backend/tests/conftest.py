"""Test isolation shared by every test.

- Each test runs from its own empty temporary folder, which is also its base
  folder (``AUTOPOSTER_BASE_DIR``), with ``AUTOPOSTER_DATA_DIR`` unset. The
  application's default base folder is ``backend/`` whatever the current folder,
  so without this override a test could read or write the project's real
  ``.env``, ``data/``, ``database/``, ``logs/``, or lock file.
- A session-wide check fails the run if the real ``database/app.db`` or
  ``database/langgraph_checkpoints.db`` changed (or was created) while the tests ran.
- No test can reach the network: httpx's real transport is blocked, and the
  LinkedIn settings are removed from the environment. Tests that talk to
  "LinkedIn" use the fake in ``linkedin_fake.py``.
"""

import hashlib
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DB = REPO_ROOT / "database" / "app.db"
REAL_CHECKPOINTS = REPO_ROOT / "database" / "langgraph_checkpoints.db"


def _fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.fixture(autouse=True)
def _run_from_isolated_folder(tmp_path_factory, monkeypatch):
    cwd = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("AUTOPOSTER_BASE_DIR", str(cwd))
    monkeypatch.delenv("AUTOPOSTER_DATA_DIR", raising=False)
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINKEDIN_TOKEN_CREATED", raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(self, request):
        raise RuntimeError(f"tests must not use the network (tried {request.method} {request.url.host})")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


@pytest.fixture(autouse=True, scope="session")
def _real_database_is_never_touched():
    before = {path: _fingerprint(path) for path in (REAL_DB, REAL_CHECKPOINTS)}
    yield
    for path, fingerprint in before.items():
        assert _fingerprint(path) == fingerprint, f"A test changed or created a real database: {path}"
