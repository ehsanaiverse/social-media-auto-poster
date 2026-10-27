"""FastAPI application factory.

A local tool only: start it with ``--host 127.0.0.1`` (see ``__main__.py``). It always
uses the project's own data folder, job database (``database/app.db``), logs, and run
lock, the same ones the CLI uses from the project root, wherever the server is started.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from social_media_auto_poster.config import Settings, load_settings
from social_media_auto_poster.main import DEFAULT_INTERVAL_S

from .routers import jobs, run, scan
from .services import JobService

# The Vite dev server (frontend/vite.config.ts). It proxies /api, so this only matters for direct calls.
FRONTEND_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

# backend/app/main.py -> the project root. Starting the server from backend/ must not
# create a second database (backend/database/app.db); there is only database/app.db.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_app(settings: Settings | None = None, *, stability_interval_s: float = DEFAULT_INTERVAL_S) -> FastAPI:
    """Build the app. Nothing is created on disk until a request needs it (``run`` creates the folders).

    Without ``settings`` it uses the project root, like the CLI run from there. Tests pass their own.
    """
    settings = settings if settings is not None else load_settings(base_dir=PROJECT_ROOT)

    app = FastAPI(
        title="Social Media Auto-Poster API",
        description="Local REST API for the folder-driven social media auto-poster. Publishing is simulated.",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(scan.router)
    app.include_router(run.router)
    app.include_router(jobs.router)
    app.state.job_service = JobService(settings, stability_interval_s)
    return app
