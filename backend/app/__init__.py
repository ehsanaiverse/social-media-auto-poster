"""Backend API server for the social media auto-poster.

Wraps the existing LangGraph+SQLite logic behind a FastAPI REST API
so a frontend can drive it from a browser.
"""

import sys
from pathlib import Path

try:  # pragma: no cover - exercised only when the package is not installed
    import social_media_auto_poster as _core  # noqa: F401
except ModuleNotFoundError:
    # Ensure backend directory is in sys.path so social_media_auto_poster is importable
    _backend = Path(__file__).resolve().parent.parent
    if _backend.is_dir() and str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))

from .main import create_app

__all__ = ["create_app"]
