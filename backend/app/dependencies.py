"""FastAPI dependency providers."""

from __future__ import annotations

from fastapi import Request

from .services import JobService


def get_job_service(request: Request) -> JobService:
    """Dependency: return the shared JobService instance from app state."""
    return request.app.state.job_service