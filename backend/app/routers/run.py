"""Run endpoint: process every complete post once through the workflow."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from social_media_auto_poster.models import Platform

from ..dependencies import get_job_service
from ..services import JobService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/run", tags=["run"])


@router.post("")
async def run(
    platform: Platform = Platform.LINKEDIN,
    service: JobService = Depends(get_job_service),
):
    """Process every complete post once. Publishing is simulated."""
    try:
        result = service.run(platform)
    except Exception as error:
        logger.exception("Run failed")
        raise HTTPException(status_code=500, detail=str(error))
    return result