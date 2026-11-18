"""Command-line entry point: ``python -m social_media_auto_poster.main <command>``.

- ``scan``: a read-only report of what ingestion would pick up, including duplicates.
- ``jobs``: read-only job history from ``database/app.db``.
- ``run``: process every complete post once through the workflow graph,
  recording each job in ``database/app.db``. Publishing is simulated unless
  ``--real`` is given; real mode is never a default or a setting (D36).
- ``resolve``: manually settle a NEEDS_REVIEW job as PUBLISHED or FAILED.
- ``linkedin-check``: check the LinkedIn token and setup; never creates a post.
- ``schedule`` / ``unschedule`` / ``schedules``: schedule a post for a date and time,
  cancel it, or list schedules (D46). Every other run skips posts with an active schedule.

The same operations are available to the local API (``backend/app``). They live
here as functions that return data (``scan_preview``, ``run_once``,
``resolve_job``, ``linkedin_status``, ``schedule_post``, ``unschedule_post``,
``list_schedules``); the command functions only print them and map the result to
an exit code, so the API and the CLI share one implementation.
"""

import argparse
import logging
import sqlite3
import sys
import time
from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from social_media_auto_poster.config import (
    DATA_DIR_VAR,
    InvalidTimezoneError,
    Settings,
    data_dir_outside_base,
    ensure_data_dirs,
    load_settings,
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
    EXIT_LOCKED,
    EXIT_OK,
    EXIT_SETUP_ERROR,
    EXIT_USAGE,
    MIN_JOB_ID_PREFIX,
    PLATFORM,
    REAL_CONFIRMATION,
    _describe_original,
    _local_time,
    _minutes,
    _relative_to_data,
    outside_warning,
)
from social_media_auto_poster.operations.logs import _run_scope, logging_to
from social_media_auto_poster.operations.scan import (
    CandidateView,
    ScanPreview,
    ScheduleView,
    _active_schedules,
    find_duplicate,
    scan_preview,
    unstable_files,
)
from social_media_auto_poster.platforms.base import Publisher
from social_media_auto_poster.platforms.linkedin.check import run_check
from social_media_auto_poster.platforms.linkedin.client import LinkedInClient
from social_media_auto_poster.platforms.linkedin.credentials import (
    CredentialsError,
    TokenState,
    load_linkedin_credentials,
)
from social_media_auto_poster.platforms.linkedin.publisher import LinkedInPublisher
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.real_mode import (
    SimulatedOnly,
    archive_without_history,
    posts_only_simulated,
)
from social_media_auto_poster.services.reconcile import Recovery, reconcile_interrupted_jobs
from social_media_auto_poster.services.run_lock import RunLockHeldError, single_run_lock
from social_media_auto_poster.services.scheduling import (
    CONTENT_LABELS,
    CONTENT_MISSING,
    MIN_LEAD,
    DueSelection,
    ScheduleError,
    ScheduleTimeError,
    check_schedulable,
    check_time_format,
    content_state,
    parse_publish_at,
    select_due,
)
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobNotFoundError, JobRecord, JobRepository
from social_media_auto_poster.storage.schedules import (
    DuplicateScheduleError,
    ScheduleNotFoundError,
    ScheduleRecord,
    ScheduleRepository,
    schedules_available,
)

logger = logging.getLogger(__name__)

# Outcome counters reported on the "Run finished" log line, in this order.
RUN_COUNTS = (
    "simulated", "published", "failed", "needs_review", "duplicates",
    "waiting", "pending", "ambiguous", "recovered", "resumed", "errors", "scheduled",
)

RUN_EPILOG = """\
exit codes:
  0  run completed; every job was simulated, a duplicate, or waiting, or there was nothing to do
  1  setup error (data folder, job database, checkpoint store, or crash recovery
     could not complete), or run --real refused before publishing anything
     (LinkedIn token missing, expired, or rejected; posts only simulated so
     far; or published posts in archive/ that the job database does not
     know about)
  2  invalid command or option
  3  run completed, but at least one job ended FAILED, NEEDS_REVIEW, or ERROR
     (including jobs resumed after a crash), or an interrupted job was recovered
     as NEEDS_REVIEW
  4  another run is in progress
"""

RESOLVE_EPILOG = """\
exit codes:
  0  resolved
  1  refused: no such job, ambiguous ID prefix, not NEEDS_REVIEW, empty post ID,
     a simulated job marked --published, or no job database
  2  invalid command or option (e.g. an ID prefix shorter than 6 characters)
  4  a run is in progress
"""

SCHEDULE_EPILOG = """\
The time is in AUTOPOSTER_TIMEZONE (default Asia/Karachi) and must be 5 minutes to 1 year ahead.
Without --real the schedule is simulated. Until it is due, every run skips the post.

exit codes:
  0  scheduled
  1  refused, nothing changed: no such post, partner file missing, ambiguous name,
     a file still being copied, invalid post, unusable time, content already
     handled or scheduled, post already scheduled, or the job database could not
     be opened; also a bad AUTOPOSTER_TIMEZONE
  2  invalid command or option (including a time not in the form YYYY-MM-DD HH:MM)
  4  a run is in progress
"""

UNSCHEDULE_EPILOG = """\
exit codes:
  0  cancelled (the files stay in incoming/; the next run processes the post)
  1  the post has no active schedule, or the job database could not be opened
  2  invalid command or option
  4  a run is in progress
"""

RECONFIRM_EPILOG = """\
Use it when schedules or scan shows CHANGED or FILE MISSING: a changed post is never published
automatically, because its schedule confirmed the old content only.

exit codes:
  0  re-confirmed (the old SCHEDULED schedule is CANCELLED; an old INVALID one stays INVALID)
  1  refused, nothing changed: nothing to re-confirm (no SCHEDULED or INVALID schedule), the
     same reasons as schedule, or the old time is less than 5 minutes ahead (give --at)
  2  invalid command or option (including a time not in the form YYYY-MM-DD HH:MM)
  4  a run is in progress
"""

SCHEDULES_EPILOG = """\
Read-only: never locks, writes, creates or upgrades the job database.

exit codes:
  0  listed (also when there are no schedules or no job database yet)
  1  the job database could not be read
  2  invalid command or option
"""


# Data returned by the functions below (the API serializes these; the CLI prints them).


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


@dataclass(frozen=True)
class ResolveResult:
    job: JobRecord
    status: JobStatus
    post_id: str | None
    note: str


class ResolveError(Exception):
    """A resolve was refused. ``kind`` is one of: blank_post_id, no_database, not_found, ambiguous,
    not_needs_review, simulated_published, no_longer_needs_review."""

    def __init__(self, kind: str, message: str, details: tuple[str, ...] = ()):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details


@dataclass(frozen=True)
class LinkedInStatus:
    token_set: bool
    state: str  # missing, expired, expiring, valid, or unknown (no creation date given).
    expires_on: date | None
    days_left: int | None
    member: tuple[str, str] | None  # (name, author URN); only after a check that LinkedIn accepted.
    problems: tuple[str, ...]  # Plain texts; never contains the token.
    unreachable: bool  # check=True and LinkedIn could not be reached.


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


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``), run the command, and return its exit code."""
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except InvalidTimezoneError as error:  # A settings problem: say so plainly, never a traceback.
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m social_media_auto_poster.main",
        description="Folder-driven social media auto-poster. LinkedIn only; publishing is simulated unless run --real.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    scan = commands.add_parser(
        "scan",
        help="Report what is in the LinkedIn incoming folder. Read-only.",
        description=(
            "Report candidates (with stability and validation), pending files, and ambiguous stems. "
            "Never moves or publishes."
        ),
    )
    scan.add_argument(
        "--dir",
        type=Path,
        help="Folder to scan instead of the configured LinkedIn incoming folder.",
    )
    _add_interval_option(scan)
    scan.add_argument(
        "--real",
        action="store_true",
        help="Check duplicates against real-mode history, as run --real would.",
    )
    scan.set_defaults(handler=_scan)

    jobs = commands.add_parser(
        "jobs",
        help="Show job history from the job database. Read-only.",
        description="List recent jobs, or show one job in full. Never creates or changes the database.",
    )
    jobs.add_argument("--last", type=_positive_int, default=20, help="How many jobs to list (default: 20).")
    jobs.add_argument(
        "--status", type=str.upper, choices=[s.value for s in JobStatus], help="Only jobs with this status."
    )
    jobs.add_argument("--id", dest="job_id", help="Show every detail of one job (an ID prefix is enough).")
    jobs.set_defaults(handler=_jobs)

    resolve = commands.add_parser(
        "resolve",
        help="Manually resolve a NEEDS_REVIEW job after checking the platform.",
        description=(
            "Settle a NEEDS_REVIEW job once you have checked the platform. --published keeps its content "
            "blocked from re-posting; --failed lets it be retried. Files are never moved."
        ),
        epilog=RESOLVE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    resolve.add_argument("job_id", type=_job_id_prefix, help=f"Job ID, or its first {MIN_JOB_ID_PREFIX}+ characters.")
    outcome = resolve.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--published", metavar="POST_ID", help="It was published; record the platform's post ID.")
    outcome.add_argument("--failed", action="store_true", help="It was not published; allow a retry.")
    resolve.set_defaults(handler=_resolve)

    run = commands.add_parser(
        "run",
        help="Process every complete post once. Simulated unless --real.",
        description=(
            "Run each complete post in the configured LinkedIn incoming folder through the workflow. "
            "Invalid or failed posts move to failed/ with an error report; simulated posts stay in incoming/; "
            "posts published with --real move to archive/."
        ),
        epilog=RUN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_interval_option(run)
    run.add_argument("--verbose", action="store_true", help="Also show INFO log lines in the terminal.")
    run.add_argument(
        "--real",
        action="store_true",
        help="Publish to LinkedIn for real. Never a default: it must be given on every real run.",
    )
    run.add_argument(
        "--publish-simulated",
        action="store_true",
        help="With --real: also publish posts whose content was already simulated (refused otherwise).",
    )
    run.add_argument(
        "--scheduled",
        action="store_true",
        help="Publish only scheduled posts that are due (at most one per run), nothing else. "
             "With --real: real schedules; without: simulated schedules. For Windows Task Scheduler.",
    )
    run.set_defaults(handler=_run)

    check = commands.add_parser(
        "linkedin-check",
        help="Check the LinkedIn token and setup. Never creates a post.",
        description=(
            "Check that the access token in backend\\.env is set, not expired, and accepted by LinkedIn. "
            "With --upload-test, also upload a small test image (never used in a post) and check that "
            "its processing status can be read."
        ),
    )
    check.add_argument(
        "--upload-test",
        action="store_true",
        help="Also upload a generated test image (no post) and read its processing status.",
    )
    check.set_defaults(handler=_linkedin_check)

    schedule = commands.add_parser(
        "schedule",
        help="Schedule a post in the LinkedIn incoming folder for a date and time.",
        description=(
            "Schedule one post (its name, without extension) for a date and time. The post is checked now: it "
            "must be complete, valid, and not already handled or scheduled. Its files must not change before then."
        ),
        epilog=SCHEDULE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    schedule.add_argument("post", help="The post's name: its file names without the extension.")
    schedule.add_argument(
        "--at", required=True, type=_schedule_time, metavar='"YYYY-MM-DD HH:MM"',
        help="When to publish, in AUTOPOSTER_TIMEZONE (default Asia/Karachi), 24-hour clock.",
    )
    schedule.add_argument(
        "--real",
        action="store_true",
        help="Schedule a real post to LinkedIn. Never a default: without it the schedule is simulated.",
    )
    _add_interval_option(schedule)
    schedule.set_defaults(handler=_schedule)

    unschedule = commands.add_parser(
        "unschedule",
        help="Cancel a post's schedule. Its files stay in incoming/.",
        description="Cancel the active schedule of one post. The files are not moved; the next run processes the post.",
        epilog=UNSCHEDULE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    unschedule.add_argument("post", help="The post's name: its file names without the extension.")
    unschedule.set_defaults(handler=_unschedule)

    reconfirm = commands.add_parser(
        "reconfirm",
        help="Re-confirm a post's schedule for its current files (after a change, or to change mode or time).",
        description=(
            "Replace the post's active schedule (or its latest INVALID one) with a new schedule that covers the "
            "files as they are now. Every schedule check is made again. The time stays the same unless --at is "
            "given. Without --real the new schedule is simulated, even if the old one was real."
        ),
        epilog=RECONFIRM_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reconfirm.add_argument("post", help="The post's name: its file names without the extension.")
    reconfirm.add_argument(
        "--at", type=_schedule_time, metavar='"YYYY-MM-DD HH:MM"',
        help="A new time, in AUTOPOSTER_TIMEZONE (default: keep the old time).",
    )
    reconfirm.add_argument(
        "--real",
        action="store_true",
        help="Real post to LinkedIn. Never carried over: without it the new schedule is simulated.",
    )
    _add_interval_option(reconfirm)
    reconfirm.set_defaults(handler=_reconfirm)

    schedules = commands.add_parser(
        "schedules",
        help="List scheduled posts. Read-only.",
        description="List active schedules, soonest first, or every schedule with --all.",
        epilog=SCHEDULES_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    schedules.add_argument("--all", action="store_true", help="Also list finished schedules (newest first).")
    schedules.add_argument(
        "--status", type=str.upper, choices=[s.value for s in ScheduleStatus], help="Only schedules with this status."
    )
    schedules.add_argument("--last", type=_positive_int, default=50, help="With --all or --status: how many (default: 50).")
    schedules.set_defaults(handler=_schedules)
    return parser


def _schedule_time(text: str) -> str:
    """argparse type for --at: the form only. Zone, clock changes and the 5 minutes / 1 year rule come later."""
    try:
        return check_time_format(text)
    except ScheduleTimeError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _add_interval_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--interval",
        type=_non_negative_float,
        default=DEFAULT_INTERVAL_S,
        help=f"Seconds to watch each file for changes (default: {DEFAULT_INTERVAL_S}; 0 checks only for locks).",
    )


# scan


def _scan(args: argparse.Namespace) -> int:
    settings = load_settings()
    mode = _mode(args)
    folder = (args.dir if args.dir is not None else settings.incoming_dir(PLATFORM)).resolve()
    with logging_to(verbose=False, log_file=None):
        try:
            preview = scan_preview(
                settings, folder=folder, mode=mode, interval_s=args.interval, check_archive=args.dir is None
            )
        except FileNotFoundError:
            print(f"Error: incoming folder not found: {folder}", file=sys.stderr)
            print(f"Create it, or set {DATA_DIR_VAR}, or pass --dir.", file=sys.stderr)
            return EXIT_SETUP_ERROR

        if args.dir is None and data_dir_outside_base(settings):
            print(outside_warning(settings), file=sys.stderr)
        print(f"Scanning LinkedIn incoming: {folder}" + ("   (duplicate check: real mode)" if args.real else ""))
        result = preview.scan
        if not (result.candidates or result.pending or result.ambiguous):
            print("\nNothing found.")
        else:
            _print_candidates(preview.candidates, mode, settings.timezone)
            _print_pending_and_ambiguous(result)
        if preview.missing_schedules:
            _print_missing_schedules(preview.missing_schedules, settings.timezone)
        if preview.interrupted_jobs:
            print(f"\nNote: {preview.interrupted_jobs} interrupted job(s) will be recovered by the next run.")
        if mode is PublishMode.REAL and args.dir is None and preview.unknown_archive:
            print(f"\nNote: run --real would refuse: {len(preview.unknown_archive)} folder(s) in archive/, but no "
                  "real jobs in the job database.")
    print("\nDry run: nothing was moved or published.")
    return EXIT_OK


def _print_candidates(views: tuple[CandidateView, ...], mode: PublishMode, zone: ZoneInfo) -> None:
    if not views:
        return
    pairs = [f"{v.candidate.media_path.name} + {v.candidate.caption_path.name}" for v in views]
    stability = [_stability_label(v) for v in views]
    stem_width = max(len(v.candidate.stem) for v in views)
    pair_width = max(len(p) for p in pairs)
    stability_width = max(len(s) for s in stability)
    print(f"\nCANDIDATES ({len(views)})")
    for view, pair, stable in zip(views, pairs, stability):
        if view.schedule is not None:  # Runs skip it until it is due; say if it will not be published.
            state = "valid" if view.valid else f"INVALID: {'; '.join(view.errors)}"
            last = f"{state}   SCHEDULED for {_local_time(view.schedule.publish_at, zone)} ({view.schedule.mode})"
            if view.schedule_content in CONTENT_LABELS:
                last += f" – {CONTENT_LABELS[view.schedule_content]}"
        elif not view.valid:
            last = f"INVALID: {'; '.join(view.errors)}"
        elif view.stable:
            last = f"valid   {_duplicate_label(view, mode)}"
        else:
            last = "valid"  # Not hashed while it may still be changing.
        print(f"  {view.candidate.stem:<{stem_width}}   {pair:<{pair_width}}   {stable:<{stability_width}}   {last}")


def _print_missing_schedules(views: tuple[ScheduleView, ...], zone: ZoneInfo) -> None:
    width = max(len(v.schedule.stem) for v in views)
    print(f"\nSCHEDULED, FILES MISSING ({len(views)}) - these will not be published; re-confirm or unschedule them")
    for view in views:
        s = view.schedule
        label = CONTENT_LABELS.get(view.content or "", CONTENT_LABELS[CONTENT_MISSING])
        print(f"  {s.stem:<{width}}   {_local_time(s.publish_at, zone)} ({s.mode})   {label}")


def _duplicate_label(view: CandidateView, mode: PublishMode) -> str:
    """Whether ``run`` (in ``mode``) would skip this post as a duplicate. Same check as the graph, read-only."""
    if view.duplicate is not None:
        return f"DUPLICATE {_describe_original(view.duplicate)}"
    if view.duplicate_check_failed:
        return "could not read files to check for duplicates"
    if view.simulated is not None:
        return (f"new, but already simulated as job {view.simulated.job_id[:8]} "
                "(run --real refuses it without --publish-simulated)")
    return "new"


def _stability_label(view: CandidateView) -> str:
    if view.stable:
        return "stable"
    return f"NOT STABLE ({' and '.join(view.unstable)} changing or locked)"


# jobs


def _jobs(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        conn = open_app_db_read_only(settings.app_db_path)
    except FileNotFoundError:
        print(f"No jobs yet (no job database at {settings.app_db_path}).")
        return EXIT_OK
    except (SchemaTooNewError, sqlite3.Error) as error:
        print(f"Error: could not read the job database: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    try:
        repository = JobRepository(conn)
        if args.job_id:
            return _show_job(repository, args.job_id.strip().lower())
        status = JobStatus(args.status) if args.status else None
        records = repository.list_recent(args.last, status=status)
        if not records:
            print(f"No jobs with status {status}." if status else "No jobs yet.")
            return EXIT_OK
        _print_job_table(records)
        return EXIT_OK
    except sqlite3.Error as error:
        print(f"Error: could not read the job database: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    finally:
        conn.close()


def _print_job_table(records: list[JobRecord]) -> None:
    stem_width = max(20, *(len(r.stem) for r in records))
    print(f"  {'JOB':<9} {'CREATED (UTC)':<20} {'STEM':<{stem_width}} {'MODE':<10} STATUS")
    for r in records:
        print(f"  {r.job_id[:8]:<9} {r.created_at:%Y-%m-%d %H:%M:%S}  {r.stem:<{stem_width}} {r.mode:<10} {r.status}")
        for error in r.errors:
            print(f"  {'':<9} - {error}")


def _show_job(repository: JobRepository, prefix: str) -> int:
    matches = repository.find_by_id_prefix(prefix)
    if not matches:
        print(f"Error: no job ID starts with '{prefix}'.", file=sys.stderr)
        return EXIT_SETUP_ERROR
    if len(matches) > 1:
        print(f"Error: '{prefix}' matches {len(matches)} jobs; use a longer prefix:", file=sys.stderr)
        for r in matches:
            print(f"  {r.job_id}  {r.stem}  {r.status}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    [r] = matches
    print(f"Job:             {r.job_id}")
    print(f"Post:            {r.stem} ({r.media_name} + {r.caption_name})")
    print(f"Platform:        {r.platform}")
    print(f"Mode:            {r.mode}")
    print(f"Status:          {r.status}")
    print(f"Created:         {r.created_at:%Y-%m-%d %H:%M:%S} UTC")
    print(f"Updated:         {r.updated_at:%Y-%m-%d %H:%M:%S} UTC")
    print(f"Media SHA-256:   {r.media_sha256 or '-'}")
    print(f"Caption SHA-256: {r.caption_sha256 or '-'}")
    print(f"Post ID:         {r.platform_post_id or '-'}")
    print(f"Destination:     {r.destination or '-'}")
    print("Errors:          " + ("(none)" if not r.errors else ""))
    for error in r.errors:
        print(f"  - {error}")
    return EXIT_OK


# resolve


def resolve_job(settings: Settings, job_prefix: str, *, published_post_id: str | None = None) -> ResolveResult:
    """Settle a NEEDS_REVIEW job: PUBLISHED with ``published_post_id``, or FAILED (retryable) without it.

    Takes the run lock, so it never changes the database during a run. Raises ResolveError for a
    refusal (nothing is changed) and RunLockHeldError if a run is in progress.
    """
    if published_post_id is not None and not published_post_id.strip():
        raise ResolveError("blank_post_id", "--published needs the platform's post ID (it must not be empty).")
    with single_run_lock(settings.lock_file), _run_scope():
        if not settings.app_db_path.is_file():
            raise ResolveError("no_database", f"no job database at {settings.app_db_path}; nothing to resolve.")
        try:
            conn = open_app_db(settings.app_db_path)
        except (SchemaTooNewError, sqlite3.Error) as error:
            raise ResolveError("no_database", f"could not open the job database: {error}") from None
        try:
            return _resolve_in_database(JobRepository(conn), job_prefix, published_post_id)
        finally:
            conn.close()


def _resolve_in_database(repository: JobRepository, prefix: str, published_post_id: str | None) -> ResolveResult:
    matches = repository.find_by_id_prefix(prefix)
    if not matches:
        raise ResolveError("not_found", f"no job ID starts with '{prefix}'. Nothing was changed.")
    if len(matches) > 1:
        details = tuple(f"  {r.job_id}  {r.stem}  {r.status}" for r in matches)
        raise ResolveError(
            "ambiguous", f"'{prefix}' matches {len(matches)} jobs; use a longer prefix:", details
        )

    [job] = matches
    name = f"job {job.job_id[:8]} ({job.stem})"
    if job.status is not JobStatus.NEEDS_REVIEW:
        raise ResolveError(
            "not_needs_review",
            f"{name} is {job.status}; only NEEDS_REVIEW jobs can be resolved. Nothing was changed.",
        )
    published = published_post_id is not None
    if published and job.mode is PublishMode.SIMULATED:
        raise ResolveError(
            "simulated_published",
            f"{name} is a simulated job; nothing was sent, so it cannot have been published. "
            "Resolve it with --failed. Nothing was changed.",
        )

    status = JobStatus.PUBLISHED if published else JobStatus.FAILED
    post_id = published_post_id.strip() if published else None
    stamp = f"{clock.utc_now():%Y-%m-%dT%H:%M:%SZ}"
    note = (
        f"resolved manually as PUBLISHED (post ID {post_id}) at {stamp}"
        if published
        else f"resolved manually as FAILED at {stamp}"
    )
    try:
        repository.resolve_needs_review(job.job_id, status=status, note=note, platform_post_id=post_id)
    except JobNotFoundError:  # Cannot happen while we hold the lock; refuse rather than guess.
        raise ResolveError("no_longer_needs_review", f"{name} is no longer NEEDS_REVIEW. Nothing was changed.") from None
    logger.info(
        "Job resolved manually: job_id=%s platform=%s mode=%s stem=%s status=%s post_id=%s",
        job.job_id, job.platform, job.mode, job.stem, status, post_id,
    )
    return ResolveResult(job=job, status=status, post_id=post_id, note=note)


def _resolve(args: argparse.Namespace) -> int:
    settings = load_settings()
    published = args.published
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            result = resolve_job(settings, args.job_id, published_post_id=published)
    except ResolveError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        for line in error.details:
            print(line, file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED

    name = f"job {result.job.job_id[:8]} ({result.job.stem})"
    print(f"Resolved {name} as {result.status}.")
    print(f"Note recorded: {result.note}")
    _print_resolve_next_steps(result.job, result.status, settings)
    return EXIT_OK


def _print_resolve_next_steps(job: JobRecord, status: JobStatus, settings: Settings) -> None:
    files = f"{job.media_name} and {job.caption_name}"
    incoming = settings.incoming_dir(job.platform)
    if job.destination:
        print(f"Files: {files} are in {job.destination}")
    else:
        print(f"Files: {files} were not moved by the app (check {incoming})")

    if status is JobStatus.PUBLISHED:
        print("Next: the content stays blocked from re-posting. Keep or delete the files as you like.")
    elif job.destination:
        print(f"Next: to retry, fix the post if needed, move {files} back into {incoming}, then run again.")
    else:
        print("Next: if the files are still in incoming/, the next run will process them again.")


# schedule, unschedule, schedules (D46)


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
    warnings = _token_warnings(settings, publish_at, now) if mode is PublishMode.REAL else ()
    return ScheduleResult(record, _local_time(publish_at, settings.timezone), warnings)


def unschedule_post(settings: Settings, name: str) -> ScheduleRecord:
    """Cancel the active schedule of ``name``; its files are not touched. Raises ScheduleError or RunLockHeldError."""
    with single_run_lock(settings.lock_file), _run_scope():
        if not _has_schedules(settings):  # No database, or one from before schedules: nothing to cancel, nothing upgraded.
            raise ScheduleError("not_scheduled", f"'{name.strip()}' has no active schedule.")
        conn = _open_for_schedules(settings)
        try:
            schedules = ScheduleRepository(conn)
            active = schedules.find_active(platform=PLATFORM, stem=name.strip())
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
    settings: Settings, name: str, *, real: bool, at: str | None = None, interval_s: float = DEFAULT_INTERVAL_S
) -> ReconfirmResult:
    """Re-confirm a post's schedule with its current content (D46): a new schedule replaces the old one.

    Works on the post's active (SCHEDULED) schedule, changed or not, or else on its latest schedule if that
    is INVALID. Every check ``schedule`` makes is made again. Real only with ``real`` (never carried over).
    The time stays the old one unless ``at`` is given. The old SCHEDULED schedule is cancelled and the new
    one created in one transaction; an old INVALID one stays as it is. Takes the run lock. Raises
    ScheduleError (nothing changed) or RunLockHeldError.
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
        warnings.extend(_token_warnings(settings, publish_at, now))
    return ReconfirmResult(old, new, _local_time(publish_at, settings.timezone), tuple(warnings))


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


def _token_warnings(settings: Settings, publish_at: datetime, now: datetime) -> tuple[str, ...]:
    """Token problems a real schedule will meet, read from backend/.env only: no LinkedIn call, never the token."""
    try:
        credentials = load_linkedin_credentials(settings.base_dir, today=now.astimezone(settings.timezone).date())
    except CredentialsError as error:
        return (f"{error}; real runs are refused without it, so this post will not be published until it is set.",)
    expires_on = credentials.expires_on
    if expires_on is None:
        return ("LINKEDIN_TOKEN_CREATED is not set, so the token's expiry cannot be checked against this schedule.",)
    publish_day = publish_at.astimezone(settings.timezone).date()
    if publish_day >= expires_on:
        return (
            f"the LinkedIn access token expires on {expires_on}, on or before the scheduled day ({publish_day}); "
            "renew it before then, or the post will not be published.",
        )
    return ()


def _schedule(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            result = schedule_post(settings, args.post, args.at, real=args.real, interval_s=args.interval)
    except ScheduleError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED

    record = result.schedule
    kind = ("REAL: to be published to LinkedIn" if record.mode is PublishMode.REAL
            else "simulated: nothing will be sent to LinkedIn")
    print(f"Scheduled '{record.stem}' for {result.local_time} ({record.publish_at:%Y-%m-%d %H:%M} UTC), {kind}.")
    print(f"Schedule ID: {record.schedule_id[:8]}")
    print("Keep both files unchanged in incoming/ until then; a changed file makes the schedule INVALID.")
    print("A scheduled run publishes it at or after that time: run --scheduled (add --real for a real post), "
          "for example every 15 minutes from Windows Task Scheduler. Until then every other run skips it.")
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return EXIT_OK


def _unschedule(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            cancelled = unschedule_post(settings, args.post)
    except ScheduleError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED
    when = _local_time(cancelled.publish_at, settings.timezone)
    print(f"Cancelled the schedule of '{cancelled.stem}' (was {when}, {cancelled.mode}).")
    print("Its files stay in incoming/; the next run processes it like any other post "
          "(run --real would publish it).")
    return EXIT_OK


def _schedules(args: argparse.Namespace) -> int:
    settings = load_settings()
    status = ScheduleStatus(args.status) if args.status else None
    try:
        listing = list_schedules(settings, include_all=args.all, status=status, limit=args.last)
    except ScheduleError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    if not listing.available:
        print("No schedules yet.")
        return EXIT_OK
    if not listing.schedules:
        print(f"No schedules with status {status}." if status else
              "No schedules yet." if args.all else "No active schedules (use --all to see finished ones).")
        return EXIT_OK
    _print_schedule_table(listing.schedules, settings.timezone)
    return EXIT_OK


def _print_schedule_table(views: tuple[ScheduleView, ...], zone: ZoneInfo) -> None:
    stem_width = max(16, *(len(v.schedule.stem) for v in views))
    when = f"WHEN ({zone.key})"
    print(f"  {'ID':<9} {'POST':<{stem_width}} {'MODE':<10} {when:<22} {'UTC':<17} STATUS")
    for view in views:
        r = view.schedule
        local = f"{r.publish_at.astimezone(zone):%Y-%m-%d %H:%M}"
        print(f"  {r.schedule_id[:8]:<9} {r.stem:<{stem_width}} {r.mode:<10} {local:<22} "
              f"{r.publish_at:%Y-%m-%d %H:%M}  {r.status}")
        if view.content in CONTENT_LABELS:  # Found by reading the files now; the row itself is unchanged.
            print(f"  {'':<9} - {CONTENT_LABELS[view.content]}")
        if r.job_id:
            print(f"  {'':<9} - job {r.job_id[:8]}")
        if r.reason:
            print(f"  {'':<9} - {r.reason}")


def _reconfirm(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            result = reconfirm_post(settings, args.post, real=args.real, at=args.at, interval_s=args.interval)
    except ScheduleError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED

    new, old = result.new, result.old
    kind = ("REAL: to be published to LinkedIn" if new.mode is PublishMode.REAL
            else "simulated: nothing will be sent to LinkedIn")
    old_now = "now CANCELLED" if old.status is ScheduleStatus.SCHEDULED else f"which stays {old.status}"
    print(f"Re-confirmed '{new.stem}' for {result.local_time} ({new.publish_at:%Y-%m-%d %H:%M} UTC), {kind}.")
    print(f"Schedule ID: {new.schedule_id[:8]} (replaces {old.schedule_id[:8]}, {old_now}).")
    print("The schedule now covers the files as they are; keep them unchanged until then.")
    print("A scheduled run publishes it at or after that time: run --scheduled (add --real for a real post), "
          "for example every 15 minutes from Windows Task Scheduler. Until then every other run skips it.")
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return EXIT_OK


# run


def run_once(
    settings: Settings,
    mode: PublishMode,
    *,
    publish_simulated: bool = False,
    interval_s: float = DEFAULT_INTERVAL_S,
    scheduled: bool = False,
) -> RunReport:
    """One complete run: the lock, crash recovery, real-mode safeguards, and every complete post once.

    Simulated unless ``mode`` is REAL. With ``scheduled`` (D46) it handles only the due schedules of
    ``mode`` (at most one published per run; more than 6 hours late -> MISSED) and nothing else in
    incoming/. Returns a RunReport; a refusal is data in the report, not an exception. Raises
    RunLockHeldError if another run holds the lock, and OSError if the run cannot be set up. Log
    handlers are the caller's job (the CLI and the API each attach their own).
    """
    started = time.monotonic()
    counts: Counter[str] = Counter()
    report: RunReport | None = None
    with single_run_lock(settings.lock_file), _run_scope():
        logger.info("Run started: platform=%s mode=%s%s", PLATFORM, mode, " scheduled=yes" if scheduled else "")
        if data_dir_outside_base(settings):
            logger.warning(outside_warning(settings).removeprefix("Warning: "))
        try:
            report = _run_locked(settings, mode, publish_simulated, interval_s, counts, scheduled)
            return report
        finally:
            exit_code = report.exit_code if report is not None else EXIT_SETUP_ERROR
            logger.info(
                "Run finished: %s exit_code=%d duration_s=%.2f",
                " ".join(f"{key}={counts[key]}" for key in RUN_COUNTS),
                exit_code,
                time.monotonic() - started,
            )


def _run(args: argparse.Namespace) -> int:
    mode = _mode(args)
    if args.scheduled and args.publish_simulated:
        print("Error: --scheduled cannot be combined with --publish-simulated: a scheduled run publishes only "
              "scheduled posts, each confirmed when it was scheduled.", file=sys.stderr)
        return EXIT_USAGE
    if args.publish_simulated and mode is not PublishMode.REAL:
        print("Error: --publish-simulated only applies together with --real.", file=sys.stderr)
        return EXIT_USAGE
    settings = load_settings()
    try:
        with logging_to(verbose=args.verbose, log_file=settings.log_file):
            # flush: when output is piped, stdout is buffered and stderr warnings would overtake it.
            banner = "   (REAL MODE: posts are published to LinkedIn)" if mode is PublishMode.REAL else ""
            if args.scheduled:
                banner += "   (scheduled run: only scheduled posts that are due)"
            print(f"Processing LinkedIn incoming: {settings.incoming_dir(PLATFORM)}{banner}", flush=True)
            report = run_once(settings, mode, publish_simulated=args.publish_simulated, interval_s=args.interval,
                              scheduled=args.scheduled)
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED
    except OSError as error:
        print(f"Error: could not set up the run: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    _print_run_report(report, settings)
    return report.exit_code


def _run_locked(
    settings: Settings,
    mode: PublishMode,
    publish_simulated: bool,
    interval_s: float,
    counts: Counter[str],
    scheduled: bool = False,
) -> RunReport:
    """Everything a run does while holding the lock, after "Run started" is logged."""
    if mode is PublishMode.REAL:
        # A real run never creates the data folder: a missing one usually means a wrong AUTOPOSTER_DATA_DIR.
        missing = missing_platform_dirs(settings, PLATFORM)
        if missing:
            logger.error("Real run refused: data folders missing: %s", ", ".join(str(path) for path in missing))
            text = "\n".join([
                f"\nREFUSED: the data folder {settings.data_dir} does not have its LinkedIn folders:",
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
            selection = select_due(schedules.due(platform=PLATFORM, mode=mode, now=now), mode=mode, now=now)
        if selection is not None and not selection.publish:
            # Nothing to publish: no real publisher, so no token check and no LinkedIn call (D46).
            publisher: Publisher | RunRefusal = _NothingToPublish()
        else:
            # Real-mode checks come before anything changes, so a refused run leaves everything as it was.
            publisher = _publisher_for(mode, settings, repository, stack)
        if isinstance(publisher, RunRefusal):
            return _refused(mode, publisher)
        try:
            # Resume first: it settles what checkpoints can prove; reconciliation handles the rest.
            resumed = resume_interrupted_jobs(checkpointer, repository, settings, schedules=schedules)
            recoveries = resumed.recoveries + reconcile_interrupted_jobs(
                repository, settings, skip=resumed.skip_reconcile
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
                context, schedules, selection, now, tuple(resumed_lines), recoveries, earlier_problems, counts
            )
        return _process(
            context, schedules, publish_simulated, tuple(resumed_lines), recoveries, earlier_problems, counts,
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
        footer=_footer(context.mode, tuple(lines)),
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


class RealPublishingUnavailable(Exception):
    """Real publishing cannot start; the message says why."""


def _linkedin_client(access_token: str) -> LinkedInClient:
    return LinkedInClient(access_token)


def _real_publisher(settings: Settings) -> Publisher:
    """The LinkedIn publisher, once the token in backend/.env is present, not expired, and accepted.

    The token is checked with LinkedIn (``/v2/userinfo``) here, before anything changes.
    """
    today = clock.today()
    try:
        credentials = load_linkedin_credentials(settings.base_dir, today=today)
    except CredentialsError as error:
        raise RealPublishingUnavailable(str(error)) from None
    state = credentials.state(today)
    if state is TokenState.EXPIRED:
        raise RealPublishingUnavailable(credentials.describe(today))
    if state in (TokenState.EXPIRING, TokenState.UNKNOWN):
        logger.warning("LinkedIn token: %s", credentials.describe(today))
    publisher = LinkedInPublisher(_linkedin_client(credentials.access_token))
    problem = publisher.check_access()
    if problem is not None:
        publisher.close()
        raise RealPublishingUnavailable(problem)
    return publisher


def _publisher_for(
    mode: PublishMode, settings: Settings, repository: JobRepository, stack: ExitStack
) -> Publisher | RunRefusal:
    """The publisher for this run, or the refusal that stops a real run before anything changes."""
    if mode is PublishMode.SIMULATED:
        return Simulator()
    unknown = archive_without_history(settings, repository, PLATFORM)
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
        publisher = _real_publisher(settings)
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
) -> RunReport:
    settings, platform = context.settings, PLATFORM
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
    scheduled, candidates = _hold_scheduled(result.candidates, schedules, context)
    counts["scheduled"] = len(scheduled)

    if context.mode is PublishMode.REAL and not publish_simulated:
        simulated_only = posts_only_simulated(candidates, context.repository)
        if simulated_only:
            logger.error("Real run refused: %d post(s) were only simulated so far", len(simulated_only))
            return RunReport(
                mode=context.mode,
                exit_code=EXIT_SETUP_ERROR,
                refusal=RunRefusal("simulated_only", _simulated_refusal_text(simulated_only)),
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
        footer=_footer(context.mode, lines),
        scheduled=scheduled,
    )


def _hold_scheduled(
    candidates: tuple[PostCandidate, ...], schedules: ScheduleRepository, context: WorkflowContext
) -> tuple[tuple[ScheduledPost, ...], tuple[PostCandidate, ...]]:
    """Split off the posts that have an active schedule: they stay in incoming/ and get no job."""
    active = _active_schedules(schedules)
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


def _simulated_refusal_text(simulated_only: list[SimulatedOnly]) -> str:
    width = max(len(item.candidate.stem) for item in simulated_only)
    lines = [f"\nREFUSED ({len(simulated_only)}) - these posts were only simulated; a real run would publish them now"]
    for item in simulated_only:
        job = item.simulated_job
        lines.append(f"  {item.candidate.stem:<{width}}   simulated as job {job.job_id[:8]} ({job.created_at:%Y-%m-%d})")
    lines.append(
        "\nNothing was published. Remove the posts you do not want on LinkedIn from incoming/, "
        "or run again with --real --publish-simulated to publish them."
    )
    return "\n".join(lines)


def _footer(mode: PublishMode, lines: tuple[JobLine, ...]) -> str:
    """The run's closing line: what this run sent to LinkedIn, in plain words."""
    if mode is PublishMode.SIMULATED:
        return "\nSimulation mode: nothing was sent to LinkedIn in this run."
    published = sum(line.outcome == "published" for line in lines)
    footer = f"\nReal mode: {published} post(s) published to LinkedIn in this run."
    needs_review = sum(line.outcome == "needs_review" for line in lines)
    if needs_review:
        footer += f" {needs_review} need review: check LinkedIn, then use the resolve command."
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


def _print_run_report(report: RunReport, settings: Settings) -> None:
    """The terminal view of a run. Everything it shows is already in ``report``."""
    if report.resumed:
        _print_jobs(list(report.resumed), title="RESUMED", note="jobs finished after an earlier crash")
    if report.recoveries:
        _print_recoveries(list(report.recoveries))
    if report.scheduled:
        _print_scheduled(report.scheduled)
    if report.refusal is not None:
        if report.refusal.text:
            stream = sys.stdout if report.refusal.kind == "simulated_only" else sys.stderr
            print(report.refusal.text, file=stream)
        return
    if report.scan is None:  # Setup error: the log says what happened.
        return
    if report.scheduled_run:
        _print_scheduled_run(report)
    if report.jobs:
        _print_jobs(list(report.jobs))
    elif report.scheduled_run:
        if not report.missed:
            print("\nNo scheduled post is due.")
    else:
        print("\nNothing else to process." if report.scheduled else "\nNothing to process.")
    _print_pending_and_ambiguous(report.scan)
    print(report.footer)
    if any(line.label == DUPLICATE_LABEL for line in report.jobs):
        print("Duplicates stay in incoming/; delete them when you no longer need them.")
    print(f"Log: {settings.log_file}")


def _print_scheduled_run(report: RunReport) -> None:
    def late(post: ScheduledPost) -> str:
        minutes = _minutes(post.late_by) if post.late_by is not None else 0
        return "on time" if minutes <= 0 else f"{minutes} min late"

    for title, note, posts in (
        ("DUE", "carried out by this run (one per run)", report.due),
        ("MISSED", "more than 6 hours late; not published, nothing sent (schedule them again)", report.missed),
        ("WAITING", "also due; one post per run, so they go in the next scheduled runs", report.waiting),
    ):
        if posts:
            width = max(len(p.stem) for p in posts)
            print(f"\n{title} ({len(posts)}) - {note}")
            for post in posts:
                print(f"  {post.stem:<{width}}   {post.publish_at_local} ({post.mode}), {late(post)}")


def _print_scheduled(posts: tuple[ScheduledPost, ...]) -> None:
    width = max(len(post.stem) for post in posts)
    print(f"\nSCHEDULED ({len(posts)}) - left in incoming/ for their scheduled time")
    for post in posts:
        print(f"  {post.stem:<{width}}   {post.publish_at_local} ({post.mode})")


def _print_recoveries(recoveries: list[Recovery]) -> None:
    stem_width = max(len(r.stem) for r in recoveries)
    status_width = max(len(r.status) for r in recoveries)
    print(f"\nRECOVERED ({len(recoveries)}) - jobs interrupted by an earlier crash")
    for r in recoveries:
        first, *rest = r.notes
        print(f"  {r.stem:<{stem_width}}   {r.status:<{status_width}}   ({r.mode}) {first}")
        for note in rest:
            print(f"  {'':<{stem_width}}   {'':<{status_width}}   - {note}")


def _print_jobs(lines: list[JobLine], *, title: str = "JOBS", note: str = "") -> None:
    stem_width = max(len(line.stem) for line in lines)
    label_width = max(len(line.label) for line in lines)
    print(f"\n{title} ({len(lines)})" + (f" - {note}" if note else ""))
    for line in lines:
        print(f"  {line.stem:<{stem_width}}   {line.label:<{label_width}}   {line.note}")
        for detail in line.details:
            print(f"  {'':<{stem_width}}     - {detail}")


def _mode(args: argparse.Namespace) -> PublishMode:
    """Real only when --real was given on this command line (D36)."""
    return PublishMode.REAL if args.real else PublishMode.SIMULATED


# linkedin-check


def linkedin_status(settings: Settings, *, check: bool, today: date | None = None) -> LinkedInStatus:
    """The LinkedIn token's state from backend/.env. With ``check``, also asks LinkedIn (read-only, no post)."""
    today = today or clock.today()
    try:
        credentials = load_linkedin_credentials(settings.base_dir, today=today)
    except CredentialsError as error:
        return LinkedInStatus(
            token_set=False, state="missing", expires_on=None, days_left=None, member=None,
            problems=(str(error),), unreachable=False,
        )
    state = credentials.state(today)
    expires_on = credentials.expires_on
    days_left = (expires_on - today).days if expires_on else None
    if not check or state is TokenState.EXPIRED:
        problems = (credentials.describe(today),) if state is TokenState.EXPIRED else ()
        return LinkedInStatus(
            token_set=True, state=state.value, expires_on=expires_on, days_left=days_left, member=None,
            problems=problems, unreachable=False,
        )
    with closing(_linkedin_client(credentials.access_token)) as client:
        report = run_check(credentials, client, today=today, upload_test=False)
    return LinkedInStatus(
        token_set=True, state=state.value, expires_on=expires_on, days_left=days_left, member=report.member,
        problems=tuple(report.problems), unreachable=report.unreachable,
    )


def _linkedin_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    print("LinkedIn check (no post is ever created)")
    try:
        credentials = load_linkedin_credentials(settings.base_dir, today=clock.today())
    except CredentialsError as error:
        print(f"  PROBLEM  {error}")
        print("\nNot ready for run --real: fix the problem above.")
        return EXIT_SETUP_ERROR
    with logging_to(verbose=False, log_file=None), closing(_linkedin_client(credentials.access_token)) as client:
        report = run_check(credentials, client, today=clock.today(), upload_test=args.upload_test)
    print("\n".join(report.lines))
    print("\nReady for run --real." if report.ok else "\nNot ready for run --real: fix the problems above.")
    return EXIT_OK if report.ok else EXIT_SETUP_ERROR


# shared


def _print_pending_and_ambiguous(result: ScanResult) -> None:
    if result.pending:
        print(f"\nPENDING ({len(result.pending)}) - waiting for a partner file")
        for path in result.pending:
            print(f"  {path.name}")

    if result.ambiguous:
        print(f"\nAMBIGUOUS ({len(result.ambiguous)}) - fix by hand, more than one file per stem")
        for group in result.ambiguous:
            print(f"  {group[0].stem}: {', '.join(p.name for p in group)}")


def _job_id_prefix(text: str) -> str:
    prefix = text.strip().lower()
    if len(prefix) < MIN_JOB_ID_PREFIX:
        raise argparse.ArgumentTypeError(f"use at least {MIN_JOB_ID_PREFIX} characters of the job ID")
    return prefix


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _non_negative_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
