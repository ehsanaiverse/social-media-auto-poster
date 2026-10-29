"""``GET /api/scan``: read-only preview of the LinkedIn incoming folder (``scan``)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_job_service
from ..services import FolderMissingError, JobService

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.get("")
def scan(real: bool = Query(False), service: JobService = Depends(get_job_service)) -> dict:
    """Candidates with stability, validation, and duplicates. Never moves, publishes, or creates anything."""
    try:
        return service.scan(real=real)
    except FolderMissingError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
