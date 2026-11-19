"""``GET /api/scan``: read-only preview of a platform's incoming folder (``scan``; LinkedIn by default)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_job_service, parse_platform
from ..services import FolderMissingError, JobService

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.get("")
def scan(
    real: bool = Query(False), platform: str | None = Query(None), service: JobService = Depends(get_job_service)
) -> dict:
    """Candidates with stability, validation, and duplicates. Never moves, publishes, or creates anything."""
    chosen = parse_platform(platform)
    try:
        return service.scan(real=real, platform=chosen)
    except FolderMissingError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
