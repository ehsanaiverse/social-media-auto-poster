"""The API's bridge to the core package.

Every operation here calls a function in ``social_media_auto_poster.main`` (or the
modules it uses), the same one the CLI command calls. This file adds no storage,
database, publishing, or duplicate logic of its own. Job history is read through
``JobRepository`` and ``open_app_db_read_only``, as the ``jobs`` command does.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from social_media_auto_poster.config import Settings, data_dir_outside_base
from social_media_auto_poster.ingestion.intake import add_post as add_post_to_incoming
from social_media_auto_poster.main import (
    DEFAULT_INTERVAL_S,
    PLATFORM,
    CandidateView,
    LinkedInStatus,
    RunReport,
    _logging_to,
    _outside_warning,
    find_duplicate,
    linkedin_status,
    resolve_job,
    run_once,
    scan_preview,
)
from social_media_auto_poster.models import JobStatus, PostCandidate, PublishMode
from social_media_auto_poster.services.reconcile import Recovery
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository

MIN_JOB_ID_PREFIX = 6


class JobDatabaseError(Exception):
    """The job database exists but cannot be read."""


class FolderMissingError(Exception):
    """The LinkedIn incoming folder does not exist."""


class JobLookupError(Exception):
    """A job ID prefix matched no job (``not_found``), several (``ambiguous``), or there is no database yet."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # not_found | ambiguous | no_database
        self.message = message


class JobService:
    """Scan, post, run, and read or resolve jobs for the API. Holds no open database connection between calls."""

    def __init__(self, settings: Settings, stability_interval_s: float = DEFAULT_INTERVAL_S) -> None:
        self._settings = settings
        self._interval_s = stability_interval_s

    @property
    def settings(self) -> Settings:
        return self._settings

    def scan(self, *, real: bool) -> dict:
        """Read-only preview of the incoming folder (``scan`` / ``scan --real``). Never moves or creates anything."""
        folder = self._settings.incoming_dir(PLATFORM)
        mode = PublishMode.REAL if real else PublishMode.SIMULATED
        try:
            preview = scan_preview(
                self._settings, folder=folder, mode=mode, interval_s=self._interval_s, check_archive=True
            )
        except FileNotFoundError:
            raise FolderMissingError(f"the incoming folder does not exist: {folder}") from None
        warnings = []
        if data_dir_outside_base(self._settings):
            warnings.append(_outside_warning(self._settings).removeprefix("Warning: "))
        if preview.interrupted_jobs:
            warnings.append(f"{preview.interrupted_jobs} interrupted job(s) will be recovered by the next run.")
        if real and preview.unknown_archive:
            warnings.append(
                f"run --real would refuse: {len(preview.unknown_archive)} folder(s) in archive/, but no real "
                "jobs in the job database."
            )
        result = preview.scan
        return {
            "platform": PLATFORM.value,
            "candidates": [_candidate_to_dict(view) for view in preview.candidates],
            "pending": [path.name for path in result.pending],
            "ambiguous": [[path.name for path in group] for group in result.ambiguous],
            "warnings": warnings,
        }

    def add_post(self, *, name: str, image: bytes, image_extension: str, caption: bytes) -> dict:
        """Write a new post into incoming/. Raises IntakeError (see ingestion/intake.py)."""
        candidate = add_post_to_incoming(
            self._settings, name=name, image=image, image_extension=image_extension, caption=caption, platform=PLATFORM
        )
        return {
            "name": candidate.stem,
            "files": {"image": candidate.media_path.name, "caption": candidate.caption_path.name},
            "duplicate_of": self._duplicate_job_id(candidate),
        }

    def run(self, *, real: bool, publish_simulated: bool) -> RunReport:
        """One run, the same as ``run`` / ``run --real``. Raises RunLockHeldError if another run is in progress."""
        mode = PublishMode.REAL if real else PublishMode.SIMULATED
        with _logging_to(verbose=False, log_file=self._settings.log_file, console=False):
            return run_once(
                self._settings, mode, publish_simulated=publish_simulated, interval_s=self._interval_s
            )

    def list_jobs(self, *, last: int, status: JobStatus | None) -> list[dict]:
        """Recent jobs, newest first. An empty list if there is no job database yet."""
        with self._history() as repository:
            if repository is None:
                return []
            return [self._job_json(record) for record in repository.list_recent(last, status=status)]

    def get_job(self, prefix: str) -> dict:
        """One job in full. Raises JobLookupError when the prefix matches none, or more than one, job."""
        with self._history() as repository:
            matches = repository.find_by_id_prefix(prefix) if repository is not None else []
        if repository is None:
            raise JobLookupError("no_database", "there is no job database yet")
        if not matches:
            raise JobLookupError("not_found", f"no job ID starts with '{prefix}'")
        if len(matches) > 1:
            raise JobLookupError("ambiguous", f"'{prefix}' matches {len(matches)} jobs; use a longer prefix")
        return self._job_json(matches[0], detail=True)

    def resolve(self, prefix: str, *, published_post_id: str | None) -> dict:
        """Settle a NEEDS_REVIEW job (``resolve``). Raises ResolveError or RunLockHeldError."""
        with _logging_to(verbose=False, log_file=self._settings.log_file, console=False):
            result = resolve_job(self._settings, prefix, published_post_id=published_post_id)
        job = result.job
        destination = self._relative(job.destination)
        files_at = destination or f"not moved by the app (check {self._settings.incoming_dir(job.platform)})"
        return {"job_id": job.job_id, "status": str(result.status), "note": result.note, "files_at": files_at}

    def linkedin(self, *, check: bool) -> LinkedInStatus:
        return linkedin_status(self._settings, check=check)

    def _job_json(self, record: JobRecord, *, detail: bool = False) -> dict:
        data = job_to_dict(record, detail=detail)
        data["destination"] = self._relative(record.destination)
        return data

    def _relative(self, destination: str | None) -> str | None:
        """Folders inside the data folder, shown relative to it: no absolute paths leave the API."""
        if destination is None:
            return None
        try:
            return str(Path(destination).relative_to(self._settings.data_dir))
        except ValueError:
            return Path(destination).name  # Not under the data folder (should not happen): the folder name only.

    def _duplicate_job_id(self, candidate: PostCandidate) -> str | None:
        with self._history() as repository:
            if repository is None:
                return None
            try:
                match = find_duplicate(candidate, repository, PublishMode.SIMULATED)
            except OSError:
                return None
            return match.job_id if match else None

    @contextmanager
    def _history(self) -> Iterator[JobRepository | None]:
        """A read-only repository for the job database, or None if there is no database yet."""
        try:
            conn = open_app_db_read_only(self._settings.app_db_path)
        except FileNotFoundError:
            yield None
            return
        except (SchemaTooNewError, sqlite3.Error) as error:
            raise JobDatabaseError(str(error)) from error
        try:
            yield JobRepository(conn)
        except sqlite3.Error as error:
            raise JobDatabaseError(str(error)) from error
        finally:
            conn.close()


def _candidate_to_dict(view: CandidateView) -> dict:
    return {
        "name": view.candidate.stem,
        "media_name": view.candidate.media_path.name,
        "caption_name": view.candidate.caption_path.name,
        "stable": view.stable,
        "unstable": list(view.unstable),
        "valid": view.valid,
        "errors": list(view.errors),
        "duplicate_of": view.duplicate.job_id if view.duplicate else None,
        "duplicate_check_failed": view.duplicate_check_failed,
        "only_simulated": view.simulated is not None,
    }


def job_to_dict(record: JobRecord, *, detail: bool = False) -> dict:
    """A job as JSON. Caption text is never stored in the job database, so it cannot appear here."""
    data = {
        "job_id": record.job_id,
        "created_at": record.created_at.isoformat(),
        "stem": record.stem,
        "mode": str(record.mode),
        "status": str(record.status),
        "errors": list(record.errors),
        "post_id": record.platform_post_id,
        "destination": record.destination,
    }
    if detail:
        data |= {
            "platform": str(record.platform),
            "media_name": record.media_name,
            "caption_name": record.caption_name,
            "media_sha256": record.media_sha256,
            "caption_sha256": record.caption_sha256,
            "updated_at": record.updated_at.isoformat(),
        }
    return data


def run_to_dict(report: RunReport) -> dict:
    """A run's outcome as JSON. ``mode`` says whether anything was sent; a simulated run is never shown as published."""
    return {
        "exit_code": report.exit_code,
        "mode": str(report.mode),
        "simulated": report.mode is PublishMode.SIMULATED,
        "resumed": [_line_to_dict(line) for line in report.resumed],
        "recovered": [_recovery_to_dict(r) for r in report.recoveries],
        "jobs": [_line_to_dict(line) for line in report.jobs],
        "pending": [path.name for path in report.scan.pending] if report.scan else [],
        "ambiguous": [[p.name for p in group] for group in report.scan.ambiguous] if report.scan else [],
        "footer": report.footer.strip(),
    }


def _line_to_dict(line) -> dict:
    return {
        "job_id": line.job_id,
        "stem": line.stem,
        "mode": line.mode,
        "status": line.status,
        "label": line.label,
        "note": line.note,
        "errors": list(line.details),
        "post_id": line.post_id,
        "destination": line.destination,
    }


def _recovery_to_dict(recovery: Recovery) -> dict:
    return {
        "job_id": recovery.job_id,
        "stem": recovery.stem,
        "mode": str(recovery.mode),
        "status": str(recovery.status),
        "notes": list(recovery.notes),
        "destination": recovery.destination,
    }
