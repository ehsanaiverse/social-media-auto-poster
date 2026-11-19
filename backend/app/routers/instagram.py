"""``GET /api/instagram/status``: the Instagram account's state. Never shows the token, a key or a URL."""

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_job_service
from ..services import JobService

router = APIRouter(prefix="/api/instagram", tags=["instagram"])


@router.get("/status")
def status(check: bool = Query(False), service: JobService = Depends(get_job_service)) -> dict:
    """Without ``check``: whether backend/.env is set up (no network). With ``check=true``: ``instagram-check``
    (read-only on Instagram: the account, the quota, and a media host round trip; never publishes)."""
    return service.instagram(check=check)
