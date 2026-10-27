"""Service layer: the API's thin bridge to the core package.

It never re-implements the workflow. ``run`` calls the same ``run_once`` as the
``run`` command (run lock, crash recovery, per-mode duplicate check, run logging),
and job history uses the same read-only repository access as the ``jobs`` command.
"""

from __future__ import annotations

import sqlite3

from social_media_auto_poster.config import Settings
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.main import DEFAULT_INTERVAL_S, RunReport, find_duplicate, run_once, unstable_files
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, ScanResult
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository

PLATFORM = Platform.LINKEDIN  # The only platform in scope, as in the CLI.


class JobDatabaseError(Exception):
    """The job database exists but cannot be read."""


class JobService:
    """Scan, run, and read job history for the API. Holds no open connection between requests."""

    def __init__(self, settings: Settings, stability_interval_s: float = DEFAULT_INTERVAL_S):
        self._settings = settings
        self._interval_s = stability_interval_s

    @property
    def settings(self) -> Settings:
        return self._settings

    def scan(self) -> dict:
        """Read-only report, like the ``scan`` command. Never creates folders or the database.

        Raises FileNotFoundError if the incoming folder does not exist.
        """
        result = scan_incoming(self._settings.incoming_dir(PLATFORM), PLATFORM)
        conn = self._open_read_only_or_none()
        try:
            repository = JobRepository(conn) if conn is not None else None
            candidates = [self._describe_candidate(c, repository) for c in result.candidates]
            interrupted = len(repository.list_by_status(JobStatus.PUBLISHING)) if repository else 0
        finally:
            if conn is not None:
                conn.close()
        return {
            "platform": PLATFORM.value,
            "candidates": candidates,
            **_pending_and_ambiguous(result),
            "interrupted_jobs": interrupted,
        }

    def run(self) -> RunReport:
        """One run through the core. Raises RunLockHeldError if a run is already in progress."""
        return run_once(self._settings, self._interval_s)

    def list_jobs(self, limit: int, status: JobStatus | None) -> list[dict]:
        """Recent jobs, newest first. An empty list if there is no job database yet."""
        conn = self._open_read_only_or_none()
        if conn is None:
            return []
        try:
            return [job_dict(r) for r in JobRepository(conn).list_recent(limit, status=status)]
        except sqlite3.Error as error:
            raise JobDatabaseError(str(error)) from error
        finally:
            conn.close()

    def find_jobs(self, prefix: str) -> list[dict]:
        """Every job whose ID starts with ``prefix`` (no wildcards), like ``jobs --id``."""
        conn = self._open_read_only_or_none()
        if conn is None:
            return []
        try:
            return [job_dict(r) for r in JobRepository(conn).find_by_id_prefix(prefix)]
        except sqlite3.Error as error:
            raise JobDatabaseError(str(error)) from error
        finally:
            conn.close()

    def _describe_candidate(self, candidate: PostCandidate, repository: JobRepository | None) -> dict:
        unstable = unstable_files(candidate, self._interval_s)
        validation = validate_candidate(candidate)
        duplicate_of = None
        # Like scan: only a stable, valid post is hashed for the duplicate check.
        if repository is not None and not unstable and validation.is_valid:
            try:
                match = find_duplicate(candidate, repository)
            except OSError:
                match = None
            duplicate_of = match.job_id if match else None
        return {
            "stem": candidate.stem,
            "media_name": candidate.media_path.name,
            "caption_name": candidate.caption_path.name,
            "stable": not unstable,
            "unstable_files": unstable,
            "valid": validation.is_valid,
            "errors": list(validation.errors),
            "duplicate_of": duplicate_of,
        }

    def _open_read_only_or_none(self) -> sqlite3.Connection | None:
        try:
            return open_app_db_read_only(self._settings.app_db_path)
        except FileNotFoundError:
            return None
        except (SchemaTooNewError, sqlite3.Error) as error:
            raise JobDatabaseError(str(error)) from error


def run_dict(report: RunReport) -> dict:
    """JSON for one run. Every run is simulated until Milestone 4, and says so."""
    return {
        "exit_code": report.exit_code,
        "simulated": True,
        "completed": report.scan is not None,
        "candidates": len(report.jobs),
        "recovered": [
            {
                "job_id": r.job_id,
                "stem": r.stem,
                "mode": r.mode.value,
                "status": r.status.value,
                "notes": list(r.notes),
                "destination": r.destination,
            }
            for r in report.recoveries
        ],
        "jobs": [
            {
                "stem": line.stem,
                "result": line.label,
                "note": line.note,
                "details": list(line.details),
                "outcome": line.outcome,
                "problem": line.problem,
            }
            for line in report.jobs
        ],
        **(_pending_and_ambiguous(report.scan) if report.scan else {"pending": [], "ambiguous": []}),
    }


def job_dict(record: JobRecord) -> dict:
    return {
        "job_id": record.job_id,
        "platform": record.platform.value,
        "mode": record.mode.value,
        "stem": record.stem,
        "media_name": record.media_name,
        "caption_name": record.caption_name,
        "media_sha256": record.media_sha256,
        "caption_sha256": record.caption_sha256,
        "status": record.status.value,
        "platform_post_id": record.platform_post_id,
        "destination": record.destination,
        "errors": list(record.errors),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _pending_and_ambiguous(result: ScanResult) -> dict:
    return {
        "pending": [p.name for p in result.pending],
        "ambiguous": [[p.name for p in group] for group in result.ambiguous],
    }
