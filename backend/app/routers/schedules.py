"""Scheduled posts (D46): ``GET/POST /api/schedules``, ``POST /api/schedules/{id}/cancel`` and ``/reconfirm``.

The same operations as the ``schedules``, ``schedule``, ``unschedule`` and ``reconfirm`` commands. Publishing a
due schedule is only done by a scheduled run (``run --scheduled``), never by the API.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from social_media_auto_poster.models import ScheduleStatus
from social_media_auto_poster.services.run_lock import RunLockHeldError
from social_media_auto_poster.services.scheduling import ScheduleError

from ..dependencies import get_job_service, parse_platform, require_json, require_real_available
from ..models import ReconfirmRequest, ScheduleRequest
from ..models.schemas import REAL_CONFIRMATION
from ..services import JobService, ScheduleLookupError

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

LOCKED = "Another run is in progress; try again later."


def _check_real(real: bool, confirm: str | None) -> None:
    """A real schedule is the per-post confirmation (D46): it needs the typed phrase, every time."""
    if real and confirm != REAL_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=f'A real schedule needs "confirm": "{REAL_CONFIRMATION}" in the request. Nothing was scheduled.',
        )


def _refused(error: ScheduleError) -> HTTPException:
    if error.kind == "no_database":
        return HTTPException(status_code=500, detail="The job database could not be read; see the log.")
    code = 409 if error.kind == "schedule_changed" else 400
    detail = error.message if not error.details else "\n".join([error.message, *error.details])
    return HTTPException(status_code=code, detail=detail)


@router.get("")
def list_schedules(
    all: bool = Query(False),
    status: str | None = Query(None),
    last: int = Query(50, ge=1, le=200),
    platform: str | None = Query(None),
    service: JobService = Depends(get_job_service),
) -> dict:
    """Active schedules (soonest first), or ``all`` / one ``status`` (newest first). Read-only: no lock.

    ``platform`` lists one platform's schedules only; without it, every platform's (as before).
    """
    only = parse_platform(platform) if platform is not None and platform.strip() else None
    wanted = None
    if status is not None and status.strip():
        try:
            wanted = ScheduleStatus(status.strip().upper())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status '{status}'.") from None
    return service.list_schedules(include_all=all, status=wanted, last=last, platform=only)


@router.post("", status_code=201, dependencies=[Depends(require_json)])
def create_schedule(body: ScheduleRequest, service: JobService = Depends(get_job_service)) -> dict:
    """Schedule a post in incoming/ for ``publish_at`` (local time). Simulated unless real + the phrase."""
    platform = parse_platform(body.platform)  # Before the run lock or anything else.
    require_real_available(platform, body.real)
    _check_real(body.real, body.confirm)
    try:
        return service.schedule(name=body.name, publish_at=body.publish_at, real=body.real, platform=platform)
    except ScheduleError as error:
        raise _refused(error) from None
    except RunLockHeldError:
        raise HTTPException(status_code=409, detail=LOCKED) from None


@router.post("/{schedule_id}/cancel", dependencies=[Depends(require_json)])
def cancel_schedule(schedule_id: str, service: JobService = Depends(get_job_service)) -> dict:
    """Cancel an active schedule. The post's files stay in incoming/."""
    try:
        return service.cancel_schedule(schedule_id)
    except ScheduleLookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except ScheduleError as error:
        raise _refused(error) from None
    except RunLockHeldError:
        raise HTTPException(status_code=409, detail=LOCKED) from None


@router.post("/{schedule_id}/reconfirm", status_code=201, dependencies=[Depends(require_json)])
def reconfirm_schedule(
    schedule_id: str, body: ReconfirmRequest, service: JobService = Depends(get_job_service)
) -> dict:
    """Replace a SCHEDULED or INVALID schedule with one for the files as they are now (same time unless given)."""
    _check_real(body.real, body.confirm)
    try:
        return service.reconfirm_schedule(schedule_id, publish_at=body.publish_at, real=body.real)
    except ScheduleLookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except ScheduleError as error:
        raise _refused(error) from None
    except RunLockHeldError:
        raise HTTPException(status_code=409, detail=LOCKED) from None

