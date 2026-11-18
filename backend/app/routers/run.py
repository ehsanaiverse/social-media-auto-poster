"""``POST /api/run``: process every complete post once (``run``). Simulated unless the request says real."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from social_media_auto_poster.services.run_lock import RunLockHeldError

from ..dependencies import get_job_service, require_json
from ..models import RunRequest
from ..models.schemas import REAL_CONFIRMATION
from ..services import JobService, run_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/run", tags=["run"])

# Refusals that stop a real run before anything is published. The message names the safeguard.
REFUSED_KINDS = {"data_folders", "archive_history", "token", "simulated_only"}


# A plain def: FastAPI runs it in a worker thread, so a run never blocks the server.
@router.post("", dependencies=[Depends(require_json)])
def run(body: RunRequest, service: JobService = Depends(get_job_service)) -> dict:
    if body.publish_simulated and not body.real:
        raise HTTPException(status_code=400, detail="publish_simulated only applies together with real: true.")
    if body.real and body.confirm != REAL_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=f'A real run needs "confirm": "{REAL_CONFIRMATION}" in the request. Nothing was published.',
        )
    try:
        report = service.run(real=body.real, publish_simulated=body.publish_simulated)
    except RunLockHeldError:
        raise HTTPException(status_code=409, detail="Another run is in progress; try again later.") from None
    except OSError:
        logger.exception("Run could not be set up")
        raise HTTPException(status_code=500, detail="The run could not be set up; see the log.") from None
    if report.refusal is not None:
        if report.refusal.kind in REFUSED_KINDS:
            detail = report.refusal.text.strip() or "The run was refused."
            raise HTTPException(status_code=412, detail=detail)
        raise HTTPException(status_code=500, detail="The run could not be set up; see the log.")
    return run_to_dict(report)
