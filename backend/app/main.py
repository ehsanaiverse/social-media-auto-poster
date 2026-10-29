"""The FastAPI application: ``create_app`` builds it. Nothing is created at import time."""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from social_media_auto_poster.config import Settings, load_settings
from social_media_auto_poster.main import DEFAULT_INTERVAL_S
from social_media_auto_poster.platforms.linkedin.client import LINKEDIN_VERSION

from .routers import jobs, linkedin, posts, run, scan
from .services import JobDatabaseError, JobService

logger = logging.getLogger(__name__)

# Only the local dashboard may call the API from a browser (Vite's default ports).
FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(settings: Settings | None = None, *, stability_interval_s: float = DEFAULT_INTERVAL_S) -> FastAPI:
    """Build the app. ``settings`` defaults to the project's own (``backend/.env`` and the environment)."""
    app = FastAPI(title="Social media auto-poster (local API)", version="0.1.0")
    app.state.job_service = JobService(settings or load_settings(), stability_interval_s)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-AutoPoster"],
    )
    app.add_exception_handler(JobDatabaseError, _database_error)

    @app.get("/api/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok", "simulated_by_default": True, "linkedin_version": LINKEDIN_VERSION}

    for router in (scan.router, run.router, jobs.router, posts.router, linkedin.router):
        app.include_router(router)
    return app


def _database_error(request: Request, error: JobDatabaseError) -> JSONResponse:
    logger.error("The job database could not be read: %s", error)
    return JSONResponse(status_code=500, content={"detail": "The job database could not be read; see the log."})
