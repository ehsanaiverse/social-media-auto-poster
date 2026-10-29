"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from social_media_auto_poster.config import ensure_data_dirs, load_settings

from .routers import jobs, run, scan
from .services import JobService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: build the shared job service and release it on exit."""
    service = JobService(app.state.settings)
    app.state.job_service = service
    logger.info("Backend started: data_dir=%s", app.state.settings.data_dir)
    yield
    service.close()
    logger.info("Backend stopped")


def create_app() -> FastAPI:
    """Build the FastAPI application with CORS, routers, and static files."""
    settings = load_settings()
    # Create data/<Platform>/{incoming,archive,failed} before anything serves them.
    ensure_data_dirs(settings)

    app = FastAPI(
        title="Social Media Auto-Poster API",
        description="REST API for the folder-driven social media auto-poster.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(scan.router)
    app.include_router(run.router)
    app.include_router(jobs.router)

    # Serve uploaded media and status images from the data directory.
    app.state.settings = settings
    app.mount("/media", StaticFiles(directory=str(settings.data_dir)), name="media")

    return app


app = create_app()

