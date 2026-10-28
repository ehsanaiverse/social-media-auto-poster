"""Application settings and the platform content-folder layout.

Settings come from environment variables, optionally read from the ``.env`` file
in the base directory. Real environment variables take precedence over ``.env``
values. Relative paths are resolved against the base directory.

The base directory is ``backend/``, found from this file's location, so every
command uses the same ``data/``, ``database/``, and ``logs/`` whichever folder it
is started from. ``AUTOPOSTER_BASE_DIR`` (a real environment variable, never read
from ``.env``) points it elsewhere; the tests and the sandbox tester use it.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from social_media_auto_poster.models import Platform

DATA_DIR_VAR = "AUTOPOSTER_DATA_DIR"
BASE_DIR_VAR = "AUTOPOSTER_BASE_DIR"
# backend/, from backend/src/social_media_auto_poster/config.py.
PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = "data"
CONTENT_FOLDERS = ("incoming", "archive", "failed")


@dataclass(frozen=True)
class Settings:
    """Resolved, absolute paths used by the application."""

    base_dir: Path
    data_dir: Path

    def platform_dir(self, platform: Platform) -> Path:
        return self.data_dir / platform.value

    def incoming_dir(self, platform: Platform) -> Path:
        return self.platform_dir(platform) / "incoming"

    def archive_dir(self, platform: Platform) -> Path:
        return self.platform_dir(platform) / "archive"

    def failed_dir(self, platform: Platform) -> Path:
        return self.platform_dir(platform) / "failed"

    @property
    def log_file(self) -> Path:
        return self.base_dir / "logs" / "autoposter.log"

    @property
    def lock_file(self) -> Path:
        return self.base_dir / "autoposter.lock"

    @property
    def app_db_path(self) -> Path:
        return self.base_dir / "database" / "app.db"

    @property
    def checkpoint_db_path(self) -> Path:
        """LangGraph execution state only; kept apart from the business records in ``app.db``."""
        return self.base_dir / "database" / "langgraph_checkpoints.db"


class ProjectDirNotFoundError(RuntimeError):
    """The package is not installed from a backend/ folder (see ``default_base_dir``)."""


def default_base_dir() -> Path:
    """The ``backend/`` folder this package was installed from (editable install).

    Refuses instead of guessing: without ``backend/pyproject.toml`` the package is
    not an editable install, and its runtime folders would land in site-packages.
    """
    if not (PROJECT_DIR / "pyproject.toml").is_file():
        raise ProjectDirNotFoundError(
            f"Cannot find the backend folder: no pyproject.toml in {PROJECT_DIR}. "
            'Install from the backend folder with: python -m pip install -e ".[dev]"'
        )
    return PROJECT_DIR


def load_settings(
    base_dir: Path | None = None,
    environ: Mapping[str, str | None] | None = None,
) -> Settings:
    """Build settings from ``environ``, or from ``.env`` plus ``os.environ`` if not given.

    The base directory is ``base_dir`` if given, else ``AUTOPOSTER_BASE_DIR``, else
    ``default_base_dir()``. Passing ``environ`` explicitly (as tests do) skips ``.env``
    and ``os.environ``. ``.env`` is read by its path in the base directory, without
    modifying ``os.environ``.
    """
    if base_dir is None:
        override = (os.environ if environ is None else environ).get(BASE_DIR_VAR)
        base_dir = Path(override) if override else default_base_dir()
    base = base_dir.resolve()
    if environ is None:
        environ = {**dotenv_values(base / ".env"), **os.environ}

    # An empty value (as in .env.example) means "use the default".
    data_dir = Path(environ.get(DATA_DIR_VAR) or DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = base / data_dir

    return Settings(base_dir=base, data_dir=data_dir.resolve())


def data_dir_outside_base(settings: Settings) -> bool:
    """Whether the data folder resolves outside the base folder (``backend/``).

    Allowed, but usually a leftover ``AUTOPOSTER_DATA_DIR`` pointing at an old location,
    so ``scan`` and ``run`` print a warning with the resolved path.
    """
    return not settings.data_dir.is_relative_to(settings.base_dir)


def missing_platform_dirs(settings: Settings, platform: Platform) -> list[Path]:
    """The platform's ``incoming``, ``archive``, and ``failed`` folders that do not exist."""
    return [settings.platform_dir(platform) / folder for folder in CONTENT_FOLDERS
            if not (settings.platform_dir(platform) / folder).is_dir()]


def ensure_data_dirs(settings: Settings) -> None:
    """Create ``incoming``, ``archive``, and ``failed`` for every platform.

    Safe to call on every startup: existing folders and their files are left untouched.
    """
    for platform in Platform:
        for folder in CONTENT_FOLDERS:
            (settings.platform_dir(platform) / folder).mkdir(parents=True, exist_ok=True)
