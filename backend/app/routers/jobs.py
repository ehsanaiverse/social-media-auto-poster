"""Job history and resolving: ``GET /api/jobs``, ``GET /api/jobs/{job_id}``, ``POST /api/jobs/{job_id}/resolve``."""

from fastapi import APIRouter, Depends, HTTPException, Query

from social_media_auto_poster.main import MIN_JOB_ID_PREFIX, ResolveError
from social_media_auto_poster.models import JobStatus
from social_media_auto_poster.services.run_lock import RunLockHeldError

from ..dependencies import get_job_service, require_json
from ..models import ResolveRequest
from ..services import JobLookupError, JobService

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

RESOLVE_STATUS_CODES = {
    "blank_post_id": 400,
    "no_database": 404,
    "not_found": 404,
    "ambiguous": 409,
    "not_needs_review": 409,
    "no_longer_needs_review": 409,
    "simulated_published": 422,
}


def _check_prefix(job_id: str) -> str:
    prefix = job_id.strip().lower()
    if len(prefix) < MIN_JOB_ID_PREFIX:
        raise HTTPException(status_code=400, detail=f"Use at least {MIN_JOB_ID_PREFIX} characters of the job ID.")
    return prefix


@router.get("")
def list_jobs(
    last: int = Query(20, ge=1, le=200),
    status: str | None = Query(None),
    service: JobService = Depends(get_job_service),
) -> list[dict]:
    """Recent jobs, newest first. Read-only; an empty list if there is no job database yet."""
    wanted = None
    if status is not None and status.strip():
        try:
            wanted = JobStatus(status.strip().upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status '{status}'.") from None
    return service.list_jobs(last=last, status=wanted)


@router.get("/{job_id}")
def get_job(job_id: str, service: JobService = Depends(get_job_service)) -> dict:
    """One job in full. The ID may be a prefix of 6 or more characters."""
    prefix = _check_prefix(job_id)
    try:
        return service.get_job(prefix)
    except JobLookupError as error:
        code = {"not_found": 404, "no_database": 404, "ambiguous": 409}[error.kind]
        raise HTTPException(status_code=code, detail=error.message) from None


@router.post("/{job_id}/resolve", dependencies=[Depends(require_json)])
def resolve(job_id: str, body: ResolveRequest, service: JobService = Depends(get_job_service)) -> dict:
    """Settle a NEEDS_REVIEW job, as ``resolve`` does. Takes the run lock (409 while a run is in progress)."""
    prefix = _check_prefix(job_id)
    if body.failed == (body.published_post_id is not None):
        raise HTTPException(
            status_code=400, detail="Send exactly one of published_post_id or failed: true."
        )
    try:
        return service.resolve(prefix, published_post_id=body.published_post_id)
    except ResolveError as error:
        code = RESOLVE_STATUS_CODES.get(error.kind, 409)
        detail = error.message if not error.details else "\n".join([error.message, *error.details])
        raise HTTPException(status_code=code, detail=detail) from None
    except RunLockHeldError:
        raise HTTPException(status_code=409, detail="Another run is in progress; try again later.") from None
