"""FastAPI dependencies: the shared JobService, and the two browser-safety checks (api-plan rule 7)."""

from __future__ import annotations

from fastapi import HTTPException, Request

from social_media_auto_poster.models import Platform
from social_media_auto_poster.operations.common import PLATFORM
from social_media_auto_poster.operations.platforms import (
    ADAPTERS,
    UnsupportedPlatform,
    adapter_for,
    real_unavailable_text,
)

from .services import JobService


def get_job_service(request: Request) -> JobService:
    """The JobService created when the app started."""
    return request.app.state.job_service


def require_json(request: Request) -> None:
    """State-changing calls must send JSON. A plain HTML form on another site cannot do that without a preflight."""
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="Send the request body as application/json.")


def require_local_marker(request: Request) -> None:
    """Uploads carry a custom header, which makes the browser check the origin first (CORS)."""
    if request.headers.get("x-autoposter") != "1":
        raise HTTPException(status_code=403, detail="This request must carry the X-AutoPoster: 1 header.")


def parse_platform(value: str | None) -> Platform:
    """The platform a request names (any case), LinkedIn when it names none.

    A name that is not a platform, or a platform without an integration (Instagram, Facebook), is refused
    with 400 here, before the request takes the run lock or touches a folder or the job database.
    """
    if value is None or not value.strip():
        return PLATFORM
    wanted = value.strip().casefold()
    supported = ", ".join(platform.value for platform in ADAPTERS)
    platform = next((p for p in Platform if p.value.casefold() == wanted), None)
    if platform is None:
        raise HTTPException(status_code=400, detail=f"Unknown platform '{value}'. Supported: {supported}.")
    try:
        adapter_for(platform)
    except UnsupportedPlatform:
        raise HTTPException(
            status_code=400, detail=f"{platform} is not supported yet. Supported: {supported}. Nothing was done."
        ) from None
    return platform


def require_real_available(platform: Platform, real: bool) -> None:
    """A real request for a platform that may not publish for real (Instagram, D49) is a 400, before anything happens."""
    refused = real_unavailable_text(platform) if real else None
    if refused is not None:
        raise HTTPException(status_code=400, detail=f"{refused[0].upper()}{refused[1:]}.")
