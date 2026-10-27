"""Run endpoint: process every complete post once through the workflow."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from social_media_auto_poster.services.run_lock import RunLockHeldError

from ..dependencies import get_job_service
from ..models import RunResponse
from ..services import JobService, run_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/run", tags=["run"])


# A plain def: FastAPI runs it in a worker thread, so a run never blocks the server.
@router.post("", response_model=RunResponse)
def run(service: JobService = Depends(get_job_service)):
    """Same as the ``run`` command. Publishing is simulated; nothing is sent to LinkedIn."""
    try:
        report = service.run()
    except RunLockHeldError:
        raise HTTPException(status_code=409, detail="Another run is in progress") from None
    except OSError:
        logger.exception("Run could not be set up")
        raise HTTPException(status_code=500, detail="Run could not be set up; see the log") from None
    if report.scan is None:  # Stopped at setup (job database or crash recovery); details are in the log.
        raise HTTPException(status_code=500, detail="Run stopped at setup; see the log")
    return run_dict(report)
