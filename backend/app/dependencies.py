"""FastAPI dependencies: the shared JobService, and the two browser-safety checks (api-plan rule 7)."""

from __future__ import annotations

from fastapi import HTTPException, Request

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
