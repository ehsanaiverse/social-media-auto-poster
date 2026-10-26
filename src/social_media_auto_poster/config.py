"""Application settings and the platform content-folder layout.

Settings come from environment variables, optionally read from a ``.env`` file
in the base directory. Real environment variables take precedence over ``.env``
values. Relative paths are resolved against the base directory, which defaults
to the current working directory (run the application from the project root).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from social_media_auto_poster.models import Platform

DATA_DIR_VAR = "AUTOPOSTER_DATA_DIR"
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
    def app_db(self) -> Path:
        return self.base_dir / "database" / "app.db"


def load_settings(
    base_dir: Path | None = None,
    environ: Mapping[str, str | None] | None = None,
) -> Settings:
    """Build settings from ``environ``, or from ``.env`` plus ``os.environ`` if not given.

    Passing ``environ`` explicitly (as tests do) skips ``.env`` and ``os.environ``.
    ``.env`` is read without modifying ``os.environ``.
    """
    base = (base_dir or Path.cwd()).resolve()
    if environ is None:
        environ = {**dotenv_values(base / ".env"), **os.environ}

    # An empty value (as in .env.example) means "use the default".
    data_dir = Path(environ.get(DATA_DIR_VAR) or DEFAULT_DATA_DIR)
    if not data_dir.is_absolute():
        data_dir = base / data_dir

    return Settings(base_dir=base, data_dir=data_dir.resolve())


def ensure_data_dirs(settings: Settings) -> None:
    """Create ``incoming``, ``archive``, and ``failed`` for every platform.

    Safe to call on every startup: existing folders and their files are left untouched.
    """
    for platform in Platform:
        for folder in CONTENT_FOLDERS:
            (settings.platform_dir(platform) / folder).mkdir(parents=True, exist_ok=True)
