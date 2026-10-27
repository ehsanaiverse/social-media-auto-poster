"""Service layer: wraps ingestion, workflow, and repository calls for the API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from social_media_auto_poster.config import Settings
from social_media_auto_poster.graph.state import WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, ScanResult
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.run_lock import RunLockHeldError, single_run_lock
from social_media_auto_poster.storage.db import connect as connect_db
from social_media_auto_poster.storage.repository import JobRepository, JobRecord

logger = logging.getLogger(__name__)


@dataclass
class JobOutcome:
    """Result of running one candidate through the graph."""

    stem: str
    status: str
    errors: list[str]
    simulated: bool
    platform_post_id: str | None
    destination: str | None
    retry_later: bool


class JobService:
    """Orchestrates scanning, running jobs, and reading job records."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._conn = connect_db(settings.app_db)
        self._repo = JobRepository(self._conn)

    @property
    def settings(self) -> Settings:
        return self._settings

    def close(self) -> None:
        """Release the SQLite connection held by this service."""
        self._conn.close()

    def scan(self, platform: Platform = Platform.LINKEDIN) -> ScanResult:
        """Scan the incoming folder for a platform."""
        return scan_incoming(self._settings.incoming_dir(platform), platform)

    def run(self, platform: Platform = Platform.LINKEDIN) -> dict:
        """Process every complete post once. Returns a summary dict."""
        try:
            with single_run_lock(self._settings.lock_file):
                return self._process(platform)
        except RunLockHeldError:
            return {"error": "Another run is in progress", "jobs": []}

    def _process(self, platform: Platform) -> dict:
        result = self.scan(platform)
        context = WorkflowContext(
            publisher=Simulator(),
            settings=self._settings,
            stability_interval_s=1.0,
        )
        outcomes: list[JobOutcome] = []
        for candidate in result.candidates:
            outcomes.append(self._run_one(candidate, context))
        return {
            "platform": platform.value,
            "candidates": len(result.candidates),
            "pending": [str(p) for p in result.pending],
            "ambiguous": [[str(p) for p in group] for group in result.ambiguous],
            "jobs": [_outcome_dict(o) for o in outcomes],
        }

    def _run_one(self, candidate: PostCandidate, context: WorkflowContext) -> JobOutcome:
        media_hash = sha256_file(candidate.media_path)
        caption_hash = sha256_file(candidate.caption_path)
        existing = self._repo.find_by_hashes(candidate.platform, media_hash, caption_hash)
        if existing is not None:
            return JobOutcome(
                stem=candidate.stem,
                status="DUPLICATE",
                errors=[],
                simulated=False,
                platform_post_id=None,
                destination=None,
                retry_later=False,
            )
        job_id = self._repo.create(
            candidate.platform, candidate.stem, candidate.media_path, candidate.caption_path,
            media_hash, caption_hash,
        )
        try:
            final = run_job(candidate, context)
        except Exception as error:
            logger.exception("Unexpected error; job abandoned")
            self._repo.update_status(job_id, JobStatus.NEEDS_REVIEW, error=str(error))
            return JobOutcome(
                stem=candidate.stem,
                status=JobStatus.NEEDS_REVIEW.value,
                errors=[f"unexpected error: {error}"],
                simulated=False,
                platform_post_id=None,
                destination=None,
                retry_later=False,
            )
        status = JobStatus(final["status"])
        self._repo.update_status(
            job_id, status,
            error="; ".join(final["errors"]) or None,
            platform_post_id=final.get("platform_post_id"),
        )
        return JobOutcome(
            stem=final["stem"],
            status=final["status"],
            errors=list(final["errors"]),
            simulated=bool(final["simulated"]),
            platform_post_id=final.get("platform_post_id"),
            destination=final.get("destination"),
            retry_later=bool(final["retry_later"]),
        )

    def list_jobs(self, platform: Platform | None = None, limit: int = 100) -> list[dict]:
        """Return recent job records as dicts."""
        if platform is None:
            rows = self._repo.list_all(limit=limit)
        else:
            rows = self._repo.list_by_platform(platform, limit=limit)
        return [_job_dict(r) for r in rows]

    def get_job(self, job_id: int) -> dict | None:
        record = self._repo.get(job_id)
        return _job_dict(record) if record else None


def _outcome_dict(outcome: JobOutcome) -> dict:
    return {
        "stem": outcome.stem,
        "status": outcome.status,
        "errors": outcome.errors,
        "simulated": outcome.simulated,
        "platform_post_id": outcome.platform_post_id,
        "destination": outcome.destination,
        "retry_later": outcome.retry_later,
    }


def _job_dict(record: JobRecord) -> dict:
    return {
        "id": record.id,
        "platform": record.platform.value,
        "stem": record.stem,
        "media_path": str(record.media_path),
        "caption_path": str(record.caption_path),
        "media_hash": record.media_hash,
        "caption_hash": record.caption_hash,
        "status": record.status.value,
        "error": record.error,
        "platform_post_id": record.platform_post_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }