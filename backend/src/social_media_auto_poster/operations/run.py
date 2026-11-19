"""The run path shared by ``run`` and the API: one complete run, or a scheduled run (``run --scheduled``).

``run_once`` takes the lock, recovers interrupted work, applies the real-mode safeguards, and runs each post
through the workflow graph. It returns a ``RunReport``; the CLI prints it and the API serializes it.
"""

import logging
import sqlite3
import time
from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from social_media_auto_poster.config import (
    DATA_DIR_VAR,
    Settings,
    data_dir_outside_base,
    ensure_data_dirs,
    missing_platform_dirs,
)
from social_media_auto_poster.graph.checkpoints import open_checkpointer
from social_media_auto_poster.graph.resume import ResumeResult, resume_interrupted_jobs
from social_media_auto_poster.graph.state import JobState, WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.models import (
    JobStatus,
    Platform,
    PostCandidate,
    PostContent,
    PublishMode,
    PublishResult,
    ScanResult,
    ScheduleStatus,
)
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations.common import (
    DEFAULT_INTERVAL_S,
    DUPLICATE_LABEL,
    EXIT_JOB_PROBLEMS,
    EXIT_OK,
    EXIT_SETUP_ERROR,
    LOGGER_NAME,
    PLATFORM,
    RealPublishingUnavailable,
    _describe_original,
    _local_time,
    _minutes,
    _relative_to_data,
    outside_warning,
)
from social_media_auto_poster.operations.logs import _run_scope
from social_media_auto_poster.operations.platforms import ADAPTERS, adapter_for
from social_media_auto_poster.operations.scan import _active_schedules
from social_media_auto_poster.platforms.base import Publisher
from social_media_auto_poster.platforms.rules import display_name
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.real_mode import SimulatedOnly, archive_without_history, posts_only_simulated
from social_media_auto_poster.services.reconcile import Recovery, reconcile_interrupted_jobs
from social_media_auto_poster.services.run_lock import single_run_lock
from social_media_auto_poster.services.scheduling import DueSelection, select_due
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db
from social_media_auto_poster.storage.jobs import JobRepository
from social_media_auto_poster.storage.schedules import ScheduleNotFoundError, ScheduleRecord, ScheduleRepository

logger = logging.getLogger(LOGGER_NAME)  # Same name as before the move, so log lines are unchanged.


# Outcome counters reported on the "Run finished" log line, in this order.
RUN_COUNTS = (
    "simulated", "published", "failed", "needs_review", "duplicates",
    "waiting", "pending", "ambiguous", "recovered", "resumed", "errors", "scheduled",
)


@dataclass(frozen=True)
class JobLine:
    """One job's outcome in a run: what happened, and the job's stored details when it has them."""

    stem: str
    label: str
    note: str
    details: tuple[str, ...]
    problem: bool  # Counts toward exit code 3.
    outcome: str  # One of RUN_COUNTS, for the "Run finished" log line.
    job_id: str | None = None  # Only for jobs that are stored (duplicates and waiting posts have none).
    status: str | None = None
    mode: str | None = None
    post_id: str | None = None
    destination: str | None = None


@dataclass(frozen=True)
class RunRefusal:
    """Why a run stopped before processing posts. ``kind``: data_folders, archive_history, token, simulated_only, setup."""

    kind: str
    text: str  # What the terminal shows; empty for "setup" (the log explains it).


@dataclass(frozen=True)
class ScheduledPost:
    """A post a run left alone because it has an active schedule (D46)."""

    stem: str
    schedule_id: str
    mode: PublishMode  # The schedule's mode, not the run's.
    publish_at: datetime  # UTC.
    publish_at_local: str  # "2026-12-01 09:00 Asia/Karachi".
    late_by: timedelta | None = None  # Scheduled runs: how long after publish_at this run handled it.


@dataclass(frozen=True)
class RunReport:
    mode: PublishMode
    exit_code: int
    refusal: RunRefusal | None
    resumed: tuple[JobLine, ...]
    recoveries: tuple[Recovery, ...]
    jobs: tuple[JobLine, ...]
    scan: ScanResult | None  # None when the run stopped before scanning.
    footer: str
    scheduled: tuple[ScheduledPost, ...] = ()  # Skipped: they have an active schedule.
    # Scheduled runs only (run --scheduled, D46):
    scheduled_run: bool = False
    due: tuple[ScheduledPost, ...] = ()  # Carried out by this run (at most one), with how late.
    missed: tuple[ScheduledPost, ...] = ()  # More than 6 hours late: marked MISSED, nothing sent.
    waiting: tuple[ScheduledPost, ...] = ()  # Also due and in time: left for the next scheduled runs.


def run_once(
    settings: Settings,
    mode: PublishMode,
    *,
    publish_simulated: bool = False,
    interval_s: float = DEFAULT_INTERVAL_S,
    scheduled: bool = False,
    platform: Platform = PLATFORM,
) -> RunReport:
    """One complete run: the lock, crash recovery, real-mode safeguards, and every complete post once.

    Simulated unless ``mode`` is REAL. With ``scheduled`` (D46) it handles only the due schedules of
    ``mode`` (at most one published per run; more than 6 hours late -> MISSED) and nothing else in
    incoming/. Returns a RunReport; a refusal is data in the report, not an exception. Raises
    RunLockHeldError if another run holds the lock, and OSError if the run cannot be set up. Log
    handlers are the caller's job (the CLI and the API each attach their own). ``platform`` (default LinkedIn)
    must have an adapter: otherwise UnsupportedPlatform is raised before the lock is taken or anything is written.
    """
    adapter_for(platform)  # Refuse an unsupported platform before anything happens.
    started = time.monotonic()
    counts: Counter[str] = Counter()
    report: RunReport | None = None
    with single_run_lock(settings.lock_file), _run_scope():
        logger.info("Run started: platform=%s mode=%s%s", platform, mode, " scheduled=yes" if scheduled else "")
        if data_dir_outside_base(settings):
            logger.warning(outside_warning(settings).removeprefix("Warning: "))
        try:
            report = _run_locked(settings, mode, publish_simulated, interval_s, counts, scheduled, platform)
            return report
        finally:
            exit_code = report.exit_code if report is not None else EXIT_SETUP_ERROR
            logger.info(
                "Run finished: %s exit_code=%d duration_s=%.2f",
                " ".join(f"{key}={counts[key]}" for key in RUN_COUNTS),
                exit_code,
                time.monotonic() - started,
            )


def _run_locked(
    settings: Settings,
    mode: PublishMode,
    publish_simulated: bool,
    interval_s: float,
    counts: Counter[str],
    scheduled: bool = False,
    platform: Platform = PLATFORM,
) -> RunReport:
    """Everything a run does while holding the lock, after "Run started" is logged."""
    if mode is PublishMode.REAL:
        # A real run never creates the data folder: a missing one usually means a wrong AUTOPOSTER_DATA_DIR.
        missing = missing_platform_dirs(settings, platform)
        if missing:
            logger.error("Real run refused: data folders missing: %s", ", ".join(str(path) for path in missing))
            text = "\n".join([
                f"\nREFUSED: the data folder {settings.data_dir} does not have its {display_name(platform)} folders:",
                *(f"  {path}" for path in missing),
                f"Real runs never create them. Check {DATA_DIR_VAR} in backend\\.env; for a new setup, run once "
                "without --real to create them. Nothing was published.",
            ])
            return _refused(mode, RunRefusal("data_folders", text))
    ensure_data_dirs(settings)
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(closing(open_app_db(settings.app_db_path)))
        except (SchemaTooNewError, sqlite3.Error) as error:
            logger.error("Could not open the job database %s: %s", settings.app_db_path, error)
            return _refused(mode, RunRefusal("setup", ""))
        try:
            checkpointer = stack.enter_context(
                open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path)
            )
        except (sqlite3.Error, ValueError) as error:
            # Fail closed: without checkpoints, a crash could not be resumed safely.
            logger.error("Could not open the checkpoint store %s: %s", settings.checkpoint_db_path, error)
            return _refused(mode, RunRefusal("setup", ""))
        repository = JobRepository(conn)
        schedules = ScheduleRepository(conn)
        selection = now = None
        if scheduled:  # Read-only: what is due decides whether a publisher (and the token check) is needed.
            now = clock.now()
            selection = select_due(schedules.due(platform=platform, mode=mode, now=now), mode=mode, now=now)
        if selection is not None and not selection.publish:
            # Nothing to publish: no real publisher, so no token check and no LinkedIn call (D46).
            publisher: Publisher | RunRefusal = _NothingToPublish()
        else:
            # Real-mode checks come before anything changes, so a refused run leaves everything as it was.
            publisher = _publisher_for(mode, settings, repository, stack, platform)
        if isinstance(publisher, RunRefusal):
            return _refused(mode, publisher)
        try:
            # Resume first: it settles what checkpoints can prove; reconciliation handles the rest.
            resumed = resume_interrupted_jobs(checkpointer, repository, settings, schedules=schedules)
            recoveries = resumed.recoveries + reconcile_interrupted_jobs(
                repository, settings, skip=resumed.skip_reconcile,
                reference_platforms=frozenset(p for p, a in ADAPTERS.items() if a.records_references),
            )
        except sqlite3.Error:
            # Never process on top of an unknown state.
            logger.exception("Could not recover interrupted jobs; nothing was processed")
            return _refused(mode, RunRefusal("setup", ""))
        resumed_lines = _resumed_lines(resumed)
        counts["resumed"] = len(resumed_lines)
        counts.update(line.outcome for line in resumed_lines)
        counts["recovered"] = len(recoveries)
        earlier_problems = any(line.problem for line in resumed_lines)
        context = WorkflowContext(
            publisher=publisher,
            mode=mode,
            repository=repository,
            settings=settings,
            stability_interval_s=interval_s,
            checkpointer=checkpointer,
            schedules=schedules,
        )
        if selection is not None:
            return _process_scheduled(
                context, schedules, selection, now, tuple(resumed_lines), recoveries, earlier_problems, counts, platform
            )
        return _process(
            context, schedules, publish_simulated, tuple(resumed_lines), recoveries, earlier_problems, counts, platform,
        )


class _NothingToPublish:
    """The publisher of a scheduled run with nothing due: never called, so no publisher is built (no token check)."""

    def publish(self, post: PostContent, /) -> PublishResult:
        raise RuntimeError("A scheduled run with nothing due must never publish.")


def _process_scheduled(
    context: WorkflowContext,
    schedules: ScheduleRepository,
    selection: DueSelection,
    now: datetime,
    resumed: tuple[JobLine, ...],
    recoveries: list[Recovery],
    earlier_problems: bool,
    counts: Counter[str],
    platform: Platform = PLATFORM,
) -> RunReport:
    """A scheduled run (D46): mark the missed schedules, then carry out at most one due schedule.

    Nothing else in incoming/ is looked at, so the "only simulated" refusal (D38) has nothing to refuse:
    every post handled here was confirmed when it was scheduled. The post is built from the file names
    stored in the schedule, so a deleted or renamed file is reported by the workflow ("file missing").
    """
    zone = context.settings.timezone
    missed: list[ScheduledPost] = []
    for schedule in selection.missed:
        late = now - schedule.publish_at
        reason = (f"more than 6 hours late (due {_local_time(schedule.publish_at, zone)}, "
                  f"{_minutes(late)} min late); nothing was sent")
        try:
            schedules.mark_missed(schedule.schedule_id, reason=reason)
        except ScheduleNotFoundError:  # Settled meanwhile (for example by startup resume).
            continue
        logger.warning(
            "Schedule missed: schedule_id=%s platform=%s mode=%s stem=%s publish_at=%s late_by_minutes=%d",
            schedule.schedule_id, schedule.platform, schedule.mode, schedule.stem,
            f"{schedule.publish_at:%Y-%m-%dT%H:%M:%SZ}", _minutes(late),
        )
        missed.append(_scheduled_post(schedule, zone, late))

    lines: list[JobLine] = []
    due: list[ScheduledPost] = []
    for item in selection.publish:
        current = schedules.get(item.schedule.schedule_id)
        if current is None or current.status is not ScheduleStatus.SCHEDULED:
            continue  # Settled meanwhile (for example by startup resume): nothing to do.
        logger.info(
            "Scheduled post due: schedule_id=%s platform=%s mode=%s stem=%s publish_at=%s late_by_minutes=%d",
            current.schedule_id, current.platform, current.mode, current.stem,
            f"{current.publish_at:%Y-%m-%dT%H:%M:%SZ}", _minutes(item.late_by),
        )
        folder = context.settings.incoming_dir(current.platform)
        candidate = PostCandidate(current.platform, current.stem, folder / current.media_name,
                                  folder / current.caption_name)
        lines.append(_run_one(candidate, context, schedule=current))
        due.append(_scheduled_post(current, zone, item.late_by))
    counts.update(line.outcome for line in lines)

    waiting = tuple(_scheduled_post(s, zone, now - s.publish_at) for s in selection.waiting)
    needs_human = any(r.status is JobStatus.NEEDS_REVIEW for r in recoveries)
    problems = needs_human or earlier_problems or bool(missed) or any(line.problem for line in lines)
    return RunReport(
        mode=context.mode,
        exit_code=EXIT_JOB_PROBLEMS if problems else EXIT_OK,
        refusal=None,
        resumed=resumed,
        recoveries=tuple(recoveries),
        jobs=tuple(lines),
        scan=ScanResult(candidates=(), pending=(), ambiguous=()),
        footer=_footer(context.mode, tuple(lines), platform),
        scheduled_run=True,
        due=tuple(due),
        missed=tuple(missed),
        waiting=waiting,
    )


def _scheduled_post(schedule: ScheduleRecord, zone: ZoneInfo, late_by: timedelta | None = None) -> ScheduledPost:
    return ScheduledPost(
        stem=schedule.stem,
        schedule_id=schedule.schedule_id,
        mode=schedule.mode,
        publish_at=schedule.publish_at,
        publish_at_local=_local_time(schedule.publish_at, zone),
        late_by=late_by,
    )


def _refused(mode: PublishMode, refusal: RunRefusal) -> RunReport:
    return RunReport(
        mode=mode, exit_code=EXIT_SETUP_ERROR, refusal=refusal, resumed=(), recoveries=(), jobs=(), scan=None, footer=""
    )


def _publisher_for(
    mode: PublishMode, settings: Settings, repository: JobRepository, stack: ExitStack, platform: Platform = PLATFORM
) -> Publisher | RunRefusal:
    """The publisher for this run, or the refusal that stops a real run before anything changes."""
    if mode is PublishMode.SIMULATED:
        return Simulator()
    unknown = archive_without_history(settings, repository, platform)
    if unknown:
        logger.error("Real run refused: archive holds %d post folder(s) but the job database has no real jobs", len(unknown))
        lines = [
            f"\nREFUSED: archive/ holds {len(unknown)} published post folder(s), but the job database "
            f"({settings.app_db_path}) has no record of any real job.",
            "The database may have been replaced or lost; without it, posts could be published twice.",
            "Restore your backup of app.db, or move those folders out of archive/ if they are not this "
            "database's posts. Nothing was published.",
            *(f"  {folder.name}" for folder in unknown[:10]),
        ]
        return RunRefusal("archive_history", "\n".join(lines))
    try:
        publisher = adapter_for(platform).real_publisher(settings)
    except RealPublishingUnavailable as error:
        logger.error("Real run refused: %s", error)
        return RunRefusal("token", f"Error: {error}; nothing was published.")
    close = getattr(publisher, "close", None)
    if close is not None:
        stack.callback(close)
    return publisher


def _resumed_lines(resumed: ResumeResult) -> list[JobLine]:
    lines = [_describe(final, context) for final, context in resumed.resumed]
    lines += [
        JobLine(stem, "ERROR (unexpected)", "resume failed; kept for the next run", (), problem=True, outcome="errors")
        for _, stem in resumed.failed
    ]
    return lines


def _process(
    context: WorkflowContext,
    schedules: ScheduleRepository,
    publish_simulated: bool,
    resumed: tuple[JobLine, ...],
    recoveries: list[Recovery],
    earlier_problems: bool,
    counts: Counter[str],
    platform: Platform = PLATFORM,
) -> RunReport:
    settings = context.settings
    result = scan_incoming(settings.incoming_dir(platform), platform)
    counts["pending"] = len(result.pending)
    counts["ambiguous"] = len(result.ambiguous)
    logger.info(
        "Scan complete: platform=%s candidates=%d pending=%d ambiguous=%d",
        platform,
        len(result.candidates),
        len(result.pending),
        len(result.ambiguous),
    )
    # Posts with an active schedule are left alone by every run except a scheduled one (D46), whatever
    # the modes; they also do not count toward the "only simulated" refusal below.
    scheduled, candidates = _hold_scheduled(result.candidates, schedules, context, platform)
    counts["scheduled"] = len(scheduled)

    if context.mode is PublishMode.REAL and not publish_simulated:
        simulated_only = posts_only_simulated(candidates, context.repository)
        if simulated_only:
            logger.error("Real run refused: %d post(s) were only simulated so far", len(simulated_only))
            return RunReport(
                mode=context.mode,
                exit_code=EXIT_SETUP_ERROR,
                refusal=RunRefusal("simulated_only", _simulated_refusal_text(simulated_only, platform)),
                resumed=resumed,
                recoveries=tuple(recoveries),
                jobs=(),
                scan=result,
                footer="",
                scheduled=scheduled,
            )

    lines = tuple(_run_one(candidate, context) for candidate in candidates)
    counts.update(line.outcome for line in lines)
    needs_human = any(r.status is JobStatus.NEEDS_REVIEW for r in recoveries)
    problems = needs_human or earlier_problems or any(line.problem for line in lines)
    return RunReport(
        mode=context.mode,
        exit_code=EXIT_JOB_PROBLEMS if problems else EXIT_OK,
        refusal=None,
        resumed=resumed,
        recoveries=tuple(recoveries),
        jobs=lines,
        scan=result,
        footer=_footer(context.mode, lines, platform),
        scheduled=scheduled,
    )


def _hold_scheduled(
    candidates: tuple[PostCandidate, ...], schedules: ScheduleRepository, context: WorkflowContext,
    platform: Platform = PLATFORM,
) -> tuple[tuple[ScheduledPost, ...], tuple[PostCandidate, ...]]:
    """Split off the posts that have an active schedule: they stay in incoming/ and get no job."""
    active = _active_schedules(schedules, platform)
    held: list[ScheduledPost] = []
    rest: list[PostCandidate] = []
    for candidate in candidates:
        schedule = active.get(candidate.stem.casefold())
        if schedule is None:
            rest.append(candidate)
            continue
        held.append(ScheduledPost(
            stem=candidate.stem,
            schedule_id=schedule.schedule_id,
            mode=schedule.mode,
            publish_at=schedule.publish_at,
            publish_at_local=_local_time(schedule.publish_at, context.settings.timezone),
        ))
        logger.info(
            "Job skipped: platform=%s mode=%s stem=%s status=SKIPPED result=SCHEDULED schedule_id=%s "
            "schedule_mode=%s publish_at=%s",
            candidate.platform, context.mode, candidate.stem, schedule.schedule_id, schedule.mode,
            f"{schedule.publish_at:%Y-%m-%dT%H:%M:%SZ}",
        )
    return tuple(held), tuple(rest)


def _simulated_refusal_text(simulated_only: list[SimulatedOnly], platform: Platform = PLATFORM) -> str:
    width = max(len(item.candidate.stem) for item in simulated_only)
    lines = [f"\nREFUSED ({len(simulated_only)}) - these posts were only simulated; a real run would publish them now"]
    for item in simulated_only:
        job = item.simulated_job
        lines.append(f"  {item.candidate.stem:<{width}}   simulated as job {job.job_id[:8]} ({job.created_at:%Y-%m-%d})")
    lines.append(
        f"\nNothing was published. Remove the posts you do not want on {display_name(platform)} from incoming/, "
        "or run again with --real --publish-simulated to publish them."
    )
    return "\n".join(lines)


def _footer(mode: PublishMode, lines: tuple[JobLine, ...], platform: Platform = PLATFORM) -> str:
    """The run's closing line: what this run sent to the platform, in plain words."""
    name = display_name(platform)
    if mode is PublishMode.SIMULATED:
        return f"\nSimulation mode: nothing was sent to {name} in this run."
    published = sum(line.outcome == "published" for line in lines)
    footer = f"\nReal mode: {published} post(s) published to {name} in this run."
    needs_review = sum(line.outcome == "needs_review" for line in lines)
    if needs_review:
        footer += f" {needs_review} need review: check {name}, then use the resolve command."
    return footer


def _run_one(candidate: PostCandidate, context: WorkflowContext, schedule: ScheduleRecord | None = None) -> JobLine:
    try:
        final = run_job(candidate, context) if schedule is None else run_job(candidate, context, schedule=schedule)
    except Exception:  # A bug or a database failure, not a publish outcome. Keep going.
        logger.exception(
            "Unexpected error; job abandoned: platform=%s mode=%s stem=%s", candidate.platform, context.mode, candidate.stem
        )
        return JobLine(candidate.stem, "ERROR (unexpected)", "see the log for details", (), problem=True, outcome="errors")
    return _describe(final, context)


def _describe(final: JobState, context: WorkflowContext) -> JobLine:
    stem, status, errors = final["stem"], final["status"], tuple(final["errors"])
    destination = _relative_to_data(final["destination"], context.settings)
    stored = {
        "job_id": final["job_id"],
        "mode": final["mode"],
        "status": str(status),
        "post_id": final["platform_post_id"],
        "destination": destination,
    }

    if final.get("schedule_problem"):  # D46: changed or missing content; no job, nothing sent.
        note = (f"{final['schedule_problem']}; not published, files stay in incoming/ "
                "(reconfirm to schedule the files as they are now)")
        return JobLine(stem, "SCHEDULE INVALID", note, (), problem=True, outcome="schedule_invalid")
    if final["retry_later"]:
        return JobLine(stem, "WAITING (still copying)", "will retry on next run", (), problem=False, outcome="waiting")
    if final["duplicate_of"] and final.get("schedule_id"):
        note = f"duplicate {_duplicate_note(final['duplicate_of'], context)}; the schedule is INVALID"
        return JobLine(stem, "SCHEDULE INVALID", note, (), problem=True, outcome="schedule_invalid")
    if final["duplicate_of"]:
        note = _duplicate_note(final["duplicate_of"], context)
        return JobLine(stem, DUPLICATE_LABEL, note, (), problem=False, outcome="duplicates")
    if status == JobStatus.SIMULATED:
        note = "files stay in incoming/"
        return JobLine(stem, "SIMULATED (not published)", note, errors, problem=False, outcome="simulated", **stored)
    if status == JobStatus.PUBLISHED:
        note = f"archived to {destination}" if destination else "files left in incoming/"
        return JobLine(stem, status, note, errors, problem=bool(errors), outcome="published", **stored)
    note = f"moved to {destination}" if destination else "files left in incoming/"
    outcome = "needs_review" if status == JobStatus.NEEDS_REVIEW else "failed"
    return JobLine(stem, status, note, errors, problem=True, outcome=outcome, **stored)


def _duplicate_note(job_id: str, context: WorkflowContext) -> str:
    original = context.repository.get(job_id)
    if original is None:
        return "of an existing job; files left in incoming/"
    return f"{_describe_original(original)}; files left in incoming/"
