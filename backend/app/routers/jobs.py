"""Jobs endpoints: read-only job history, like the ``jobs`` command."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from social_media_auto_poster.models import JobStatus

from ..dependencies import get_job_service
from ..models import JobResponse
from ..services import JobDatabaseError, JobService

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobResponse])
def list_jobs(
    limit: int = Query(default=20, ge=1, le=500),
    status: JobStatus | None = None,
    service: JobService = Depends(get_job_service),
):
    """Recent jobs, newest first. Never creates or changes the database."""
    try:
        return service.list_jobs(limit, status)
    except JobDatabaseError:
        raise HTTPException(status_code=500, detail="Could not read the job database") from None


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, service: JobService = Depends(get_job_service)):
    """One job by its ID, or a unique ID prefix (no wildcards)."""
    prefix = job_id.strip().lower()
    try:
        matches = service.find_jobs(prefix)
    except JobDatabaseError:
        raise HTTPException(status_code=500, detail="Could not read the job database") from None
    if not matches:
        raise HTTPException(status_code=404, detail="Job not found")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"'{prefix}' matches {len(matches)} jobs; use a longer prefix")
    return matches[0]
