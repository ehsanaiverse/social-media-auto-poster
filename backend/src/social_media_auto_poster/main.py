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
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

from social_media_auto_poster.config import (
    DATA_DIR_VAR,
    InvalidTimezoneError,
    Settings,
    data_dir_outside_base,
    load_settings,
)
from social_media_auto_poster.models import JobStatus, Platform, PublishMode, ScanResult, ScheduleStatus
from social_media_auto_poster.operations import clock
from social_media_auto_poster.operations import linkedin as linkedin_ops
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
    outside_warning,
)
from social_media_auto_poster.operations.linkedin import LinkedInStatus, linkedin_status
from social_media_auto_poster.operations.logs import logging_to
from social_media_auto_poster.operations.resolve import ResolveError, resolve_job
from social_media_auto_poster.operations.run import JobLine, RunReport, ScheduledPost, run_once
from social_media_auto_poster.operations.scan import (
    CandidateView,
    ScanPreview,
    ScheduleView,
    find_duplicate,
    scan_preview,
)
from social_media_auto_poster.operations.schedules import (
    list_schedules,
    reconfirm_post,
    schedule_post,
    unschedule_post,
)
from social_media_auto_poster.platforms.linkedin.check import run_check
from social_media_auto_poster.platforms.linkedin.credentials import (
    CredentialsError,
    load_linkedin_credentials,
)
from social_media_auto_poster.services.reconcile import Recovery
from social_media_auto_poster.services.run_lock import RunLockHeldError
from social_media_auto_poster.services.scheduling import (
    CONTENT_LABELS,
    CONTENT_MISSING,
    ScheduleError,
    ScheduleTimeError,
    check_time_format,
)
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRecord, JobRepository

logger = logging.getLogger(__name__)


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


def _linkedin_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    print("LinkedIn check (no post is ever created)")
    try:
        credentials = load_linkedin_credentials(settings.base_dir, today=clock.today())
    except CredentialsError as error:
        print(f"  PROBLEM  {error}")
        print("\nNot ready for run --real: fix the problem above.")
        return EXIT_SETUP_ERROR
    with logging_to(verbose=False, log_file=None), closing(linkedin_ops._linkedin_client(credentials.access_token)) as client:
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
