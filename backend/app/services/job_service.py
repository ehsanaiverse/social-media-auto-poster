"""The API's bridge to the core package.

Every operation here calls a function in ``social_media_auto_poster.operations`` (or the
modules it uses), the same one the CLI command calls. This file adds no storage,
database, publishing, or duplicate logic of its own. Job history is read through
``JobRepository`` and ``open_app_db_read_only``, as the ``jobs`` command does.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from social_media_auto_poster.config import Settings, data_dir_outside_base
from social_media_auto_poster.ingestion.intake import add_post as add_post_to_incoming
from social_media_auto_poster.operations import instagram as instagram_ops
from social_media_auto_poster.operations import schedules as schedules_ops
from social_media_auto_poster.operations.common import DEFAULT_INTERVAL_S, PLATFORM, _local_time, outside_warning
from social_media_auto_poster.operations.linkedin import LinkedInStatus, linkedin_status
from social_media_auto_poster.operations.logs import logging_to
from social_media_auto_poster.ingestion.intake import MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, check_post_name
from social_media_auto_poster.ingestion.validation import EXPECTED_TYPE, MAX_CAPTION_BYTES, VIDEO_EXTENSIONS, validate_candidate, validate_content
from social_media_auto_poster.operations.platforms import ADAPTERS
from social_media_auto_poster.operations.common import REAL_CONFIRMATION
from social_media_auto_poster.operations.resolve import check_job, resolve_job
from social_media_auto_poster.platforms.instagram import rules as instagram_rules
from social_media_auto_poster.platforms.linkedin.caption import MAX_CAPTION_LENGTH as LINKEDIN_CAPTION_MAX
from social_media_auto_poster.platforms.rules import display_name
from social_media_auto_poster.operations.run import RunReport, run_once
from social_media_auto_poster.operations.scan import CandidateView, find_duplicate, scan_preview
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode, ScheduleStatus
from social_media_auto_poster.services.reconcile import Recovery
from social_media_auto_poster.services.scheduling import ScheduleError
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository
from social_media_auto_poster.storage.schedules import ScheduleRecord

MIN_JOB_ID_PREFIX = 6


class JobDatabaseError(Exception):
    """The job database exists but cannot be read."""


class FolderMissingError(Exception):
    """The platform's incoming folder does not exist."""


class JobLookupError(Exception):
    """A job ID prefix matched no job (``not_found``), several (``ambiguous``), or there is no database yet."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # not_found | ambiguous | no_database
        self.message = message


class ScheduleLookupError(Exception):
    """No schedule has this ID (or there is no job database with schedules yet)."""


# The operations speak to the command line; the API's caller sends JSON fields instead of options.
_CLI_WORDS = (
    ("give --at with a new time", "send publish_at with a new time"),
    ("because --real was not given", "because real was not requested"),
)


class JobService:
    """Scan, post, run, and read or resolve jobs for the API. Holds no open database connection between calls."""

    def __init__(self, settings: Settings, stability_interval_s: float = DEFAULT_INTERVAL_S) -> None:
        self._settings = settings
        self._interval_s = stability_interval_s

    @property
    def settings(self) -> Settings:
        return self._settings

    def scan(self, *, real: bool, platform: Platform = PLATFORM) -> dict:
        """Read-only preview of the incoming folder (``scan`` / ``scan --real``). Never moves or creates anything."""
        folder = self._settings.incoming_dir(platform)
        mode = PublishMode.REAL if real else PublishMode.SIMULATED
        try:
            preview = scan_preview(
                self._settings, folder=folder, mode=mode, interval_s=self._interval_s, check_archive=True,
                platform=platform,
            )
        except FileNotFoundError:
            raise FolderMissingError(f"the incoming folder does not exist: {folder}") from None
        warnings = []
        if data_dir_outside_base(self._settings):
            warnings.append(outside_warning(self._settings).removeprefix("Warning: "))
        if preview.interrupted_jobs:
            warnings.append(f"{preview.interrupted_jobs} interrupted job(s) will be recovered by the next run.")
        if real and preview.unknown_archive:
            warnings.append(
                f"run --real would refuse: {len(preview.unknown_archive)} folder(s) in archive/, but no real "
                "jobs in the job database."
            )
        result = preview.scan
        return {
            "platform": platform.value,
            "candidates": [_candidate_to_dict(view, self._settings) for view in preview.candidates],
            "pending": [path.name for path in result.pending],
            "ambiguous": [[path.name for path in group] for group in result.ambiguous],
            "warnings": warnings,
            # Active schedules whose post is no longer a complete pair in incoming/: they will not be published.
            "missing_schedules": [self._schedule_json(v.schedule, v.content) for v in preview.missing_schedules],
        }

    def add_post(
        self, *, name: str, image: bytes, image_extension: str, caption: bytes, platform: Platform = PLATFORM
    ) -> dict:
        """Write a new post into incoming/. Raises IntakeError (see ingestion/intake.py)."""
        candidate = add_post_to_incoming(
            self._settings, name=name, image=image, image_extension=image_extension, caption=caption, platform=platform
        )
        return {
            "name": candidate.stem,
            "files": {"image": candidate.media_path.name, "caption": candidate.caption_path.name},
            "duplicate_of": self._duplicate_job_id(candidate),
        }

    def add_post_stream(
        self, *, name: str, image_stream: BinaryIO, image_extension: str, caption: bytes,
        platform: Platform = PLATFORM
    ) -> dict:
        """Write a new post into incoming/ by streaming media. Never loads the whole file into memory."""
        candidate = add_post_to_incoming(
            self._settings, name=name, image_stream=image_stream, image_extension=image_extension,
            caption=caption, platform=platform
        )
        return {
            "name": candidate.stem,
            "files": {"image": candidate.media_path.name, "caption": candidate.caption_path.name},
            "duplicate_of": self._duplicate_job_id(candidate),
        }

    def run(self, *, real: bool, publish_simulated: bool, platform: Platform = PLATFORM) -> RunReport:
        """One run, the same as ``run`` / ``run --real``. Raises RunLockHeldError if another run is in progress."""
        mode = PublishMode.REAL if real else PublishMode.SIMULATED
        with logging_to(verbose=False, log_file=self._settings.log_file, console=False):
            return run_once(
                self._settings, mode, publish_simulated=publish_simulated, interval_s=self._interval_s,
                platform=platform,
            )

    def list_jobs(self, *, last: int, status: JobStatus | None, platform: Platform | None = None) -> list[dict]:
        """Recent jobs, newest first. An empty list if there is no job database yet."""
        with self._history() as repository:
            if repository is None:
                return []
            return [self._job_json(record) for record in repository.list_recent(last, status=status, platform=platform)]

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
        with logging_to(verbose=False, log_file=self._settings.log_file, console=False):
            result = resolve_job(self._settings, prefix, published_post_id=published_post_id)
        job = result.job
        destination = self._relative(job.destination)
        files_at = destination or f"not moved by the app (check {self._settings.incoming_dir(job.platform)})"
        return {"job_id": job.job_id, "status": str(result.status), "note": result.note, "files_at": files_at}

    def check(self, prefix: str) -> dict:
        """``resolve --check``: ask the job's platform, read-only. Raises ResolveError or RunLockHeldError."""
        with logging_to(verbose=False, log_file=self._settings.log_file, console=False):
            result = check_job(self._settings, prefix)
        job = result.job
        return {
            "job_id": job.job_id,
            "status": str(result.status),
            "changed": result.status is not JobStatus.NEEDS_REVIEW,
            "post_id": result.post_id,
            "reason": result.reason,
            "notes": self._texts(result.notes),
            "files_at": self._relative(result.destination or job.destination),
        }

    def platforms(self) -> list[dict]:
        """Every platform the app can use, with the rules the dashboard shows (the backend's own numbers)."""
        return [_platform_to_dict(platform, adapter.real_available) for platform, adapter in ADAPTERS.items()]

    def check_post(self, *, name: str | None, image: bytes, image_extension: str, caption: bytes,
                   platform: Platform = PLATFORM) -> dict:
        """The upload's problems for ``platform``, from the same checks as a post in incoming/. Writes nothing."""
        errors: list[str] = []
        if name is not None:
            try:
                check_post_name(name)
            except ValueError as error:
                errors.append(str(error))
        errors.extend(validate_content(platform, image_extension, image, caption).errors)
        return {"platform": platform.value, "ok": not errors, "errors": errors}

    def check_post_stream(
        self, *, name: str | None, image_stream: BinaryIO, image_extension: str, caption: bytes,
        platform: Platform = PLATFORM
    ) -> dict:
        """Check a video upload for ``platform`` by streaming it to a temporary file. Writes nothing permanent."""
        errors: list[str] = []
        if name is not None:
            try:
                check_post_name(name)
            except ValueError as error:
                errors.append(str(error))

        media_path, too_large = _copy_to_temp(image_stream, image_extension, MAX_VIDEO_BYTES)
        try:
            if too_large:
                errors.append(f"the video is larger than {MAX_VIDEO_BYTES // (1024 * 1024)} MB")
            else:
                caption_path = _write_temp(caption, ".txt")
                try:
                    candidate = PostCandidate(platform, name or "check", media_path, caption_path)
                    errors.extend(validate_candidate(candidate).errors)
                finally:
                    caption_path.unlink(missing_ok=True)
        finally:
            media_path.unlink(missing_ok=True)
        return {"platform": platform.value, "ok": not errors, "errors": errors}

    def instagram(self, *, check: bool) -> dict:
        """The Instagram card: set up or not, and with ``check`` the result of ``instagram-check``."""
        data = {"configured": instagram_ops.instagram_configured(self._settings), "token_type": instagram_ops.TOKEN_TYPE,
                "checked": False, "ok": None, "username": None, "account_id": None, "quota": None,
                "media_host_ok": None, "problems": []}
        if not check:
            return data
        with logging_to(verbose=False, log_file=self._settings.log_file, console=False):
            report = instagram_ops.instagram_check(self._settings)
        quota = None if report.quota_used is None else {"used": report.quota_used, "total": report.quota_total}
        return data | {"checked": True, "ok": report.ok, "username": report.username, "account_id": report.account_id,
                       "quota": quota, "media_host_ok": report.media_host_ok, "problems": self._texts(report.problems)}

    def linkedin(self, *, check: bool) -> LinkedInStatus:
        return linkedin_status(self._settings, check=check)

    # Schedules (D46): the same operations as ``schedules``, ``schedule``, ``unschedule`` and ``reconfirm``.

    def list_schedules(self, *, include_all: bool, status: ScheduleStatus | None, last: int,
                       platform: Platform | None = None) -> dict:
        """Schedules, read-only (never locks, writes, creates or upgrades the database)."""
        try:
            result = schedules_ops.list_schedules(self._settings, include_all=include_all, status=status, limit=last,
                                                     platform=platform)
        except ScheduleError as error:
            raise JobDatabaseError(error.message) from error
        return {
            "timezone": self._settings.timezone.key,
            "available": result.available,
            "schedules": [self._schedule_json(view.schedule, view.content) for view in result.schedules],
        }

    def schedule(self, *, name: str, publish_at: str, real: bool, platform: Platform = PLATFORM) -> dict:
        """Schedule a post. Raises ScheduleError (nothing written) or RunLockHeldError."""
        with self._schedule_errors(), logging_to(verbose=False, log_file=self._settings.log_file, console=False):
            result = schedules_ops.schedule_post(
                self._settings, name, publish_at, real=real, interval_s=self._interval_s, platform=platform
            )
        return {"schedule": self._schedule_json(result.schedule, "ok"), "warnings": self._texts(result.warnings)}

    def cancel_schedule(self, schedule_id: str) -> dict:
        """Cancel this schedule (checked again inside the run lock).

        Raises ScheduleLookupError, ScheduleError or RunLockHeldError.
        """
        with self._schedule_errors():
            record = self._find_schedule(schedule_id)
            if record.status is not ScheduleStatus.SCHEDULED:
                raise ScheduleError(
                    "not_scheduled", f"this schedule of '{record.stem}' is {record.status}; nothing to cancel."
                )
            with logging_to(verbose=False, log_file=self._settings.log_file, console=False):
                cancelled = schedules_ops.unschedule_post(
                    self._settings, record.stem, expected_schedule_id=record.schedule_id, platform=record.platform
                )
        return {"schedule": self._schedule_json(cancelled, None)}

    def reconfirm_schedule(self, schedule_id: str, *, publish_at: str | None, real: bool) -> dict:
        """Replace this schedule (SCHEDULED or INVALID) with one for the files as they are now.

        Raises ScheduleLookupError, ScheduleError or RunLockHeldError.
        """
        with self._schedule_errors():
            record = self._find_schedule(schedule_id)
            if record.status not in (ScheduleStatus.SCHEDULED, ScheduleStatus.INVALID):
                raise ScheduleError(
                    "nothing_to_reconfirm",
                    f"this schedule of '{record.stem}' is {record.status}; nothing to re-confirm. "
                    "Schedule the post again instead.",
                )
            with logging_to(verbose=False, log_file=self._settings.log_file, console=False):
                result = schedules_ops.reconfirm_post(
                    self._settings,
                    record.stem,
                    real=real,
                    at=publish_at,
                    interval_s=self._interval_s,
                    expected_schedule_id=record.schedule_id,
                    platform=record.platform,
                )
        return {
            "schedule": self._schedule_json(result.new, "ok"),
            "replaces": result.old.schedule_id,
            "warnings": self._texts(result.warnings),
        }

    def schedule_platform(self, schedule_id: str) -> Platform:
        """The platform of a schedule, read-only. Raises ScheduleLookupError for an unknown ID."""
        return self._find_schedule(schedule_id).platform

    def _find_schedule(self, schedule_id: str) -> ScheduleRecord:
        record = schedules_ops.find_schedule(self._settings, schedule_id.strip().lower())
        if record is None:
            raise ScheduleLookupError(f"no schedule has the ID '{schedule_id}'")
        return record

    @contextmanager
    def _schedule_errors(self) -> Iterator[None]:
        """Refusals as the API's caller should read them: no absolute paths, no command-line options."""
        try:
            yield
        except ScheduleError as error:
            raise ScheduleError(error.kind, self._text(error.message), tuple(self._texts(error.details))) from None

    def _schedule_json(self, record: ScheduleRecord, content: str | None) -> dict:
        """A schedule as JSON: no hashes, no paths. ``content_state`` only for an active schedule."""
        return {
            "schedule_id": record.schedule_id,
            "name": record.stem,
            "mode": str(record.mode),
            "status": str(record.status),
            "publish_at": record.publish_at.isoformat(),
            "publish_at_local": _local_time(record.publish_at, self._settings.timezone),
            "content_state": content,
            "reason": self._text(record.reason) if record.reason else None,
            "job_id": record.job_id,
            "created_at": record.created_at.isoformat(),
        }

    def _texts(self, texts) -> list[str]:
        return [self._text(text) for text in texts]

    def _text(self, text: str) -> str:
        """Folders inside the data or base folder are named relative to it, as destinations are."""
        folders = sorted({self._settings.data_dir, self._settings.base_dir}, key=lambda p: len(str(p)), reverse=True)
        for folder in folders:
            text = text.replace(f"{folder}{os.sep}", "").replace(str(folder), folder.name)
        for cli, api in _CLI_WORDS:
            text = text.replace(cli, api)
        return text

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


def _platform_to_dict(platform: Platform, real_available: bool) -> dict:
    extensions = sorted(EXPECTED_TYPE)
    rules = {"extensions": extensions, "max_image_bytes": MAX_IMAGE_BYTES, "max_caption_bytes": MAX_CAPTION_BYTES,
             "caption_max_chars": LINKEDIN_CAPTION_MAX, "caption_max_hashtags": None,
             "caption_max_mentions": None, "aspect": None}
    if platform is Platform.INSTAGRAM:
        rules.update(
            extensions=sorted([e for e in extensions if EXPECTED_TYPE[e] == "JPEG"] + [".mp4", ".mov"]),
            max_image_bytes=min(MAX_IMAGE_BYTES, instagram_rules.MAX_IMAGE_BYTES),
            max_video_bytes=MAX_VIDEO_BYTES,
            caption_max_chars=instagram_rules.MAX_CAPTION_CHARS,
            caption_max_hashtags=instagram_rules.MAX_HASHTAGS,
            caption_max_mentions=instagram_rules.MAX_MENTIONS,
            aspect={"min": "%d:%d" % instagram_rules.MIN_ASPECT, "max": "1.91:1"},
        )
    return {
        "id": platform.value.casefold(),
        "name": display_name(platform),
        "real_available": real_available,
        "confirmation_phrase": ADAPTERS[platform].confirmation_phrase if real_available else None,
        "rules": rules,
    }


def _candidate_to_dict(view: CandidateView, settings: Settings) -> dict:
    schedule = view.schedule
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
        # An active schedule: runs skip the post until a scheduled run publishes it (D46).
        "schedule": None if schedule is None else {
            "schedule_id": schedule.schedule_id,
            "mode": str(schedule.mode),
            "publish_at": schedule.publish_at.isoformat(),
            "publish_at_local": _local_time(schedule.publish_at, settings.timezone),
            "content_state": view.schedule_content,
        },
    }


def job_to_dict(record: JobRecord, *, detail: bool = False) -> dict:
    """A job as JSON. Caption text is never stored in the job database, so it cannot appear here."""
    data = {
        "job_id": record.job_id,
        "created_at": record.created_at.isoformat(),
        "stem": record.stem,
        "media_name": record.media_name,
        "mode": str(record.mode),
        "status": str(record.status),
        "errors": list(record.errors),
        "post_id": record.platform_post_id,
        "destination": record.destination,
    }
    if detail:
        data |= {
            "platform": str(record.platform),
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
        # Posts left alone because they have an active schedule (D46): never processed by this run.
        "scheduled": [
            {
                "name": post.stem,
                "schedule_id": post.schedule_id,
                "mode": str(post.mode),
                "publish_at": post.publish_at.isoformat(),
                "publish_at_local": post.publish_at_local,
            }
            for post in report.scheduled
        ],
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


def _copy_to_temp(stream: BinaryIO, suffix: str, max_bytes: int) -> tuple[Path, bool]:
    """Stream ``stream`` into a temporary file, stopping one byte past ``max_bytes``.

    Returns the file's path and whether it went over the limit. The caller deletes the file.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        path = Path(handle.name)
        written = 0
        while chunk := stream.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                return path, True
            handle.write(chunk)
    return path, False


def _write_temp(data: bytes, suffix: str) -> Path:
    """Write ``data`` to a temporary file and return its path. The caller deletes the file."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        return Path(handle.name)