"""``GET /api/linkedin/status``: the token's state from backend/.env. Never shows the token."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_job_service
from ..services import JobService

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])


@router.get("/status")
def status(check: bool = Query(False), service: JobService = Depends(get_job_service)) -> dict:
    """Without ``check``: local information only. With ``check=true``: also asks LinkedIn (read-only, no post)."""
    report = service.linkedin(check=check)
    if report.unreachable:
        raise HTTPException(status_code=502, detail="LinkedIn could not be reached; try again later.")
    return {
        "token_set": report.token_set,
        "state": report.state,
        "expires_on": report.expires_on.isoformat() if report.expires_on else None,
        "days_left": report.days_left,
        "member": {"name": report.member[0], "urn": report.member[1]} if report.member else None,
        "problems": list(report.problems),
    }
