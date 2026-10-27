"""FastAPI application factory.

A local tool only: start it with ``--host 127.0.0.1`` (see ``__main__.py``), from the
repository root so it uses the same data folder, job database, and run lock as the CLI.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from social_media_auto_poster.config import Settings, load_settings
from social_media_auto_poster.main import DEFAULT_INTERVAL_S

from .routers import jobs, run, scan
from .services import JobService

# The Vite dev server (frontend/vite.config.ts). It proxies /api, so this only matters for direct calls.
FRONTEND_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app(settings: Settings | None = None, *, stability_interval_s: float = DEFAULT_INTERVAL_S) -> FastAPI:
    """Build the app. Nothing is created on disk until a request needs it (``run`` creates the folders)."""
    settings = settings if settings is not None else load_settings()

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
