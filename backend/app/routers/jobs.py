"""Jobs endpoint: read job records from the database."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from social_media_auto_poster.models import Platform

from ..dependencies import get_job_service
from ..services import JobService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    platform: Platform | None = None,
    limit: int = 100,
    service: JobService = Depends(get_job_service),
):
    """Return recent job records."""
    return service.list_jobs(platform=platform, limit=limit)


@router.get("/{job_id}")
async def get_job(
    job_id: int,
    service: JobService = Depends(get_job_service),
):
    """Return a single job record by ID."""
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job