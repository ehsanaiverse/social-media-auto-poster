"""Scheduled posts (D46): ``schedule``, ``unschedule``, ``reconfirm`` and the read-only ``schedules`` list.

Publishing them is the scheduled run's job (``run --scheduled``); these only create, cancel, replace and list.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from social_media_auto_poster.config import Settings
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.models import PostCandidate, PublishMode, ScheduleStatus
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations.common import DEFAULT_INTERVAL_S, LOGGER_NAME, PLATFORM, _local_time
from social_media_auto_poster.operations.logs import _run_scope
from social_media_auto_poster.operations.platforms import adapter_for
from social_media_auto_poster.operations.scan import ScheduleView, unstable_files
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.services.scheduling import (
    MIN_LEAD,
    ScheduleError,
    ScheduleTimeError,
    check_schedulable,
    content_state,
    parse_publish_at,
)
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRepository
from social_media_auto_poster.storage.schedules import (
    DuplicateScheduleError,
    ScheduleNotFoundError,
    ScheduleRecord,
    ScheduleRepository,
    schedules_available,
)

logger = logging.getLogger(LOGGER_NAME)  # Same name as before the move, so log lines are unchanged.


@dataclass(frozen=True)
class ScheduleResult:
    schedule: ScheduleRecord
    local_time: str  # "2026-12-01 09:00 Asia/Karachi".
    warnings: tuple[str, ...]  # Real schedules: token problems to fix before the post is due. Never the token.


@dataclass(frozen=True)
class ScheduleList:
    schedules: tuple[ScheduleView, ...]
    available: bool  # False: no job database, or one from before schedules existed (it is not upgraded).


@dataclass(frozen=True)
class ReconfirmResult:
    old: ScheduleRecord  # As it was before: SCHEDULED (now CANCELLED) or INVALID (stays INVALID).
    new: ScheduleRecord
    local_time: str
    warnings: tuple[str, ...]  # Real -> simulated without --real; token problems for a real schedule.


def schedule_post(
    settings: Settings, name: str, at: str, *, real: bool, interval_s: float = DEFAULT_INTERVAL_S
) -> ScheduleResult:
    """Schedule the post ``name`` in LinkedIn's incoming/ for ``at`` (``YYYY-MM-DD HH:MM`` in the configured zone).

    Real only when ``real`` is true (D46). The post is checked now, as a run would check it, and the schedule
    is bound to both files' hashes. Takes the run lock and opens the job database for writing (upgrading an
    older one, with its backup). Raises ScheduleError (nothing changed) or RunLockHeldError.
    """
    mode = PublishMode.REAL if real else PublishMode.SIMULATED
    now = clock.now()
    with single_run_lock(settings.lock_file), _run_scope():
        candidate = _find_post(settings, name)
        unstable = unstable_files(candidate, interval_s)
        if unstable:
            raise ScheduleError(
                "not_stable",
                f"'{candidate.stem}' is still being copied ({' and '.join(unstable)} changing or locked); "
                "try again when both files are complete.",
            )
        try:
            publish_at = parse_publish_at(at, zone=settings.timezone, now=now)
        except ScheduleTimeError as error:
            raise ScheduleError("time", str(error)) from None
        conn = _open_for_schedules(settings)
        try:
            schedules = ScheduleRepository(conn)
            media_sha256, caption_sha256 = check_schedulable(
                candidate, mode=mode, jobs=JobRepository(conn), schedules=schedules
            )
            try:
                record = schedules.create(
                    schedule_id=str(uuid4()),
                    platform=candidate.platform,
                    mode=mode,
                    stem=candidate.stem,
                    media_name=candidate.media_path.name,
                    caption_name=candidate.caption_path.name,
                    media_sha256=media_sha256,
                    caption_sha256=caption_sha256,
                    publish_at=publish_at,
                )
            except DuplicateScheduleError:
                existing = schedules.find_active(platform=candidate.platform, stem=candidate.stem)
                when = _local_time(existing.publish_at, settings.timezone) if existing else "another time"
                raise ScheduleError(
                    "already_scheduled",
                    f"'{candidate.stem}' is already scheduled for {when}; unschedule it first to change it.",
                ) from None
        finally:
            conn.close()
        logger.info(
            "Schedule created: schedule_id=%s platform=%s mode=%s stem=%s publish_at=%s",
            record.schedule_id, record.platform, record.mode, record.stem, f"{record.publish_at:%Y-%m-%dT%H:%M:%SZ}",
        )
    warnings = adapter_for(PLATFORM).schedule_warnings(settings, publish_at, now) if mode is PublishMode.REAL else ()
    return ScheduleResult(record, _local_time(publish_at, settings.timezone), warnings)


def unschedule_post(settings: Settings, name: str, *, expected_schedule_id: str | None = None) -> ScheduleRecord:
    """Cancel the active schedule of ``name``; its files are not touched. Raises ScheduleError or RunLockHeldError.

    With ``expected_schedule_id`` (the API), the active schedule must be that one, checked inside the run lock;
    otherwise ScheduleError ``schedule_changed`` and nothing changes. The CLI passes nothing.
    """
    with single_run_lock(settings.lock_file), _run_scope():
        if not _has_schedules(settings):  # No database, or one from before schedules: nothing to cancel, nothing upgraded.
            raise ScheduleError("not_scheduled", f"'{name.strip()}' has no active schedule.")
        conn = _open_for_schedules(settings)
        try:
            schedules = ScheduleRepository(conn)
            active = schedules.find_active(platform=PLATFORM, stem=name.strip())
            if expected_schedule_id is not None and (active is None or active.schedule_id != expected_schedule_id):
                raise _schedule_changed(name.strip(), active)
            if active is None:
                raise ScheduleError("not_scheduled", f"'{name.strip()}' has no active schedule.")
            cancelled = schedules.cancel(active.schedule_id)  # Still SCHEDULED: we hold the lock.
        finally:
            conn.close()
        logger.info(
            "Schedule cancelled: schedule_id=%s platform=%s mode=%s stem=%s",
            cancelled.schedule_id, cancelled.platform, cancelled.mode, cancelled.stem,
        )
    return cancelled


def list_schedules(settings: Settings, *, include_all: bool = False, status: ScheduleStatus | None = None,
                   limit: int = 50) -> ScheduleList:
    """Schedules, read-only: active ones soonest first, or (``include_all`` / ``status``) newest first.

    Never locks, writes, creates or upgrades the job database. Raises ScheduleError if it cannot be read.
    """
    try:
        conn = open_app_db_read_only(settings.app_db_path)
    except FileNotFoundError:
        return ScheduleList((), available=False)
    except (SchemaTooNewError, sqlite3.Error) as error:
        raise ScheduleError("no_database", f"could not read the job database: {error}") from None
    try:
        if not schedules_available(conn):
            return ScheduleList((), available=False)
        schedules = ScheduleRepository(conn)
        if status is not None:
            records = schedules.list_recent(limit, status=status)
        elif include_all:
            records = schedules.list_recent(limit)
        else:
            records = schedules.list_active()
        # Active schedules: compare their files with what was confirmed (reads the files, writes nothing).
        views = tuple(
            ScheduleView(r, content_state(r, settings.incoming_dir(r.platform))
                         if r.status is ScheduleStatus.SCHEDULED else None)
            for r in records
        )
        return ScheduleList(views, available=True)
    except sqlite3.Error as error:
        raise ScheduleError("no_database", f"could not read the job database: {error}") from None
    finally:
        conn.close()


def reconfirm_post(
    settings: Settings,
    name: str,
    *,
    real: bool,
    at: str | None = None,
    interval_s: float = DEFAULT_INTERVAL_S,
    expected_schedule_id: str | None = None,
) -> ReconfirmResult:
    """Re-confirm a post's schedule with its current content (D46): a new schedule replaces the old one.

    Works on the post's active (SCHEDULED) schedule, changed or not, or else on its latest schedule if that
    is INVALID. Every check ``schedule`` makes is made again. Real only with ``real`` (never carried over).
    The time stays the old one unless ``at`` is given. The old SCHEDULED schedule is cancelled and the new
    one created in one transaction; an old INVALID one stays as it is. Takes the run lock. Raises
    ScheduleError (nothing changed) or RunLockHeldError. With ``expected_schedule_id`` (the API), the schedule
    re-confirmed must be that one, checked inside the lock (else ``schedule_changed``); the CLI passes nothing.
    """
    mode = PublishMode.REAL if real else PublishMode.SIMULATED
    now = clock.now()
    wanted = name.strip()
    with single_run_lock(settings.lock_file), _run_scope():
        if not _has_schedules(settings):
            raise ScheduleError("nothing_to_reconfirm", f"'{wanted}' has no schedule; nothing to re-confirm. Use schedule.")
        conn = _open_for_schedules(settings)
        try:
            schedules = ScheduleRepository(conn)
            old = schedules.find_active(platform=PLATFORM, stem=wanted) or schedules.latest_for_post(
                platform=PLATFORM, stem=wanted
            )
            if old is None or old.status not in (ScheduleStatus.SCHEDULED, ScheduleStatus.INVALID):
                state = f"its latest schedule is {old.status}" if old else "it has no schedule"
                raise ScheduleError(
                    "nothing_to_reconfirm", f"'{wanted}': {state}; nothing to re-confirm. Use schedule instead."
                )
            if expected_schedule_id is not None and old.schedule_id != expected_schedule_id:
                raise _schedule_changed(wanted, old)
            candidate = _find_post(settings, wanted)
            unstable = unstable_files(candidate, interval_s)
            if unstable:
                raise ScheduleError(
                    "not_stable",
                    f"'{candidate.stem}' is still being copied ({' and '.join(unstable)} changing or locked); "
                    "try again when both files are complete.",
                )
            if at is not None:
                try:
                    publish_at = parse_publish_at(at, zone=settings.timezone, now=now)
                except ScheduleTimeError as error:
                    raise ScheduleError("time", str(error)) from None
            else:
                publish_at = old.publish_at
                if publish_at < now + MIN_LEAD:
                    raise ScheduleError(
                        "time",
                        f"the old time {_local_time(publish_at, settings.timezone)} is less than 5 minutes ahead "
                        "or already past; give --at with a new time.",
                    )
            active_old = old.schedule_id if old.status is ScheduleStatus.SCHEDULED else None
            media_sha256, caption_sha256 = check_schedulable(
                candidate, mode=mode, jobs=JobRepository(conn), schedules=schedules, ignore_schedule_id=active_old
            )
            try:
                new = schedules.replace(
                    old.schedule_id,
                    cancel_old=active_old is not None,
                    schedule_id=str(uuid4()),
                    platform=candidate.platform,
                    mode=mode,
                    stem=candidate.stem,
                    media_name=candidate.media_path.name,
                    caption_name=candidate.caption_path.name,
                    media_sha256=media_sha256,
                    caption_sha256=caption_sha256,
                    publish_at=publish_at,
                )
            except (ScheduleNotFoundError, DuplicateScheduleError):  # Cannot happen while we hold the lock.
                raise ScheduleError(
                    "already_scheduled", f"'{candidate.stem}' changed while re-confirming; nothing was changed."
                ) from None
        finally:
            conn.close()
        logger.info(
            "Schedule re-confirmed: old=%s new=%s platform=%s mode=%s stem=%s publish_at=%s",
            old.schedule_id, new.schedule_id, new.platform, new.mode, new.stem, f"{new.publish_at:%Y-%m-%dT%H:%M:%SZ}",
        )
    warnings: list[str] = []
    if old.mode is PublishMode.REAL and mode is PublishMode.SIMULATED:
        warnings.append("the old schedule was REAL; this one is SIMULATED because --real was not given.")
    if mode is PublishMode.REAL:
        warnings.extend(adapter_for(PLATFORM).schedule_warnings(settings, publish_at, now))
    return ReconfirmResult(old, new, _local_time(publish_at, settings.timezone), tuple(warnings))


def find_schedule(settings: Settings, schedule_id: str) -> ScheduleRecord | None:
    """One schedule by its full ID, read-only (never creates or upgrades the database); None if there is none."""
    try:
        conn = open_app_db_read_only(settings.app_db_path)
    except FileNotFoundError:
        return None
    except (SchemaTooNewError, sqlite3.Error) as error:
        raise ScheduleError("no_database", f"could not read the job database: {error}") from None
    try:
        return ScheduleRepository(conn).get(schedule_id) if schedules_available(conn) else None
    except sqlite3.Error as error:
        raise ScheduleError("no_database", f"could not read the job database: {error}") from None
    finally:
        conn.close()


def _schedule_changed(name: str, current: ScheduleRecord | None) -> ScheduleError:
    now = f"it is now {current.schedule_id[:8]} ({current.status})" if current else "it has no active schedule now"
    return ScheduleError(
        "schedule_changed",
        f"the schedule of '{name}' changed since this page was loaded ({now}); nothing was changed. Refresh and try again.",
    )


def _find_post(settings: Settings, name: str) -> PostCandidate:
    """The complete post called ``name`` (compared without case) in LinkedIn's incoming/. Raises ScheduleError."""
    folder = settings.incoming_dir(PLATFORM)
    wanted = name.strip()
    if not folder.is_dir():
        raise ScheduleError("not_found", f"the incoming folder does not exist: {folder}")
    result = scan_incoming(folder, PLATFORM)
    key = wanted.casefold()
    for group in result.ambiguous:
        if group and group[0].stem.casefold() == key:
            names = ", ".join(path.name for path in group)
            raise ScheduleError("ambiguous", f"'{wanted}' is ambiguous ({names}); keep one image and one caption.")
    for candidate in result.candidates:
        if candidate.stem.casefold() == key:
            return candidate
    for path in result.pending:
        if path.stem.casefold() == key:
            raise ScheduleError("pending", f"'{wanted}' is waiting for its partner file ({path.name} has none yet).")
    raise ScheduleError("not_found", f"no post named '{wanted}' in {folder}.")


def _open_for_schedules(settings: Settings) -> sqlite3.Connection:
    try:
        return open_app_db(settings.app_db_path)
    except (SchemaTooNewError, sqlite3.Error) as error:
        raise ScheduleError("no_database", f"could not open the job database: {error}") from None


def _has_schedules(settings: Settings) -> bool:
    """Whether the job database exists and has the schedules table (checked read-only)."""
    try:
        conn = open_app_db_read_only(settings.app_db_path)
    except FileNotFoundError:
        return False
    except (SchemaTooNewError, sqlite3.Error) as error:
        raise ScheduleError("no_database", f"could not read the job database: {error}") from None
    try:
        return schedules_available(conn)
    finally:
        conn.close()
