"""Application settings and the platform content-folder layout.

Settings come from environment variables, optionally read from a ``.env`` file
in the base directory. Real environment variables take precedence over ``.env``
values. Relative paths are resolved against the base directory, which defaults
to the current working directory (run the application from the project root).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from social_media_auto_poster.models import Platform

DATA_DIR_VAR = "AUTOPOSTER_DATA_DIR"
DEFAULT_DATA_DIR = "data"
LINKEDIN_CLIENT_ID_VAR = "LINKEDIN_CLIENT_ID"
LINKEDIN_CLIENT_SECRET_VAR = "LINKEDIN_CLIENT_SECRET"
LINKEDIN_REDIRECT_URI_VAR = "LINKEDIN_REDIRECT_URI"
CONTENT_FOLDERS = ("incoming", "archive", "failed")


@dataclass(frozen=True)
class Settings:
    """Resolved, absolute paths used by the application, plus optional LinkedIn settings.

    The LinkedIn values are ``None`` when unset or blank; simulation never needs them.
    The client secret is excluded from ``repr()`` so it cannot leak into logs.
    """

    base_dir: Path
    data_dir: Path
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = field(default=None, repr=False)
    linkedin_redirect_uri: str | None = None

    def platform_dir(self, platform: Platform) -> Path:
        return self.data_dir / platform.value

    def incoming_dir(self, platform: Platform) -> Path:
        return self.platform_dir(platform) / "incoming"

    def archive_dir(self, platform: Platform) -> Path:
        return self.platform_dir(platform) / "archive"

    def failed_dir(self, platform: Platform) -> Path:
        return self.platform_dir(platform) / "failed"


def _optional(environ: Mapping[str, str | None], name: str) -> str | None:
    """Return the stripped value, or ``None`` if the variable is missing or blank."""
    return (environ.get(name) or "").strip() or None


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

    return Settings(
        base_dir=base,
        data_dir=data_dir.resolve(),
        linkedin_client_id=_optional(environ, LINKEDIN_CLIENT_ID_VAR),
        linkedin_client_secret=_optional(environ, LINKEDIN_CLIENT_SECRET_VAR),
        linkedin_redirect_uri=_optional(environ, LINKEDIN_REDIRECT_URI_VAR),
    )


def ensure_data_dirs(settings: Settings) -> None:
    """Create ``incoming``, ``archive``, and ``failed`` for every platform.

    Safe to call on every startup: existing folders and their files are left untouched.
    """
    for platform in Platform:
        for folder in CONTENT_FOLDERS:
            (settings.platform_dir(platform) / folder).mkdir(parents=True, exist_ok=True)
