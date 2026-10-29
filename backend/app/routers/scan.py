"""Scan endpoint: read-only report of incoming folder contents."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from social_media_auto_poster.models import Platform

from ..dependencies import get_job_service
from ..services import JobService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.get("")
async def scan_folder(
    platform: Platform = Query(default=Platform.LINKEDIN, description="Platform to scan"),
    dir: str | None = Query(default=None, description="Override incoming folder"),
    service: JobService = Depends(get_job_service),
):
    """Scan the incoming folder for a platform. Read-only."""
    try:
        result = service.scan(platform)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Incoming folder not found")
    return {
        "platform": platform.value,
        "candidates": [
            {
                "stem": c.stem,
                "media_path": str(c.media_path),
                "caption_path": str(c.caption_path),
            }
            for c in result.candidates
        ],
        "pending": [str(p) for p in result.pending],
        "ambiguous": [[str(p) for p in group] for group in result.ambiguous],
    }