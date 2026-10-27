"""Scan endpoint: read-only report of the LinkedIn incoming folder."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_job_service
from ..models import ScanResponse
from ..services import JobDatabaseError, JobService

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.get("", response_model=ScanResponse)
def scan_folder(service: JobService = Depends(get_job_service)):
    """Like the ``scan`` command: never moves, publishes, or creates anything."""
    try:
        return service.scan()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incoming folder not found") from None
    except JobDatabaseError:
        raise HTTPException(status_code=500, detail="Could not read the job database") from None
