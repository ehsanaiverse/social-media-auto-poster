"""``GET /api/platforms``: the platforms the app can use and their rules, so the dashboard never copies them."""

from fastapi import APIRouter, Depends

from ..dependencies import get_job_service
from ..services import JobService

router = APIRouter(prefix="/api/platforms", tags=["platforms"])


@router.get("")
def list_platforms(service: JobService = Depends(get_job_service)) -> list[dict]:
    """Each platform: id, name, real_available, confirmation_phrase (null while real is off) and rules."""
    return service.platforms()
