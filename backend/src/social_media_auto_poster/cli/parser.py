"""The command line's arguments: the parser, its help texts, and the checks on argument values."""

import argparse
from pathlib import Path

from social_media_auto_poster.cli.commands import (
    _jobs,
    _linkedin_check,
    _reconfirm,
    _resolve,
    _run,
    _scan,
    _schedule,
    _schedules,
    _unschedule,
)
from social_media_auto_poster.models import JobStatus, Platform, ScheduleStatus
from social_media_auto_poster.operations.common import DEFAULT_INTERVAL_S, MIN_JOB_ID_PREFIX
from social_media_auto_poster.services.scheduling import ScheduleTimeError, check_time_format


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
     a simulated job marked --published, or no job database; with --check also a
     platform that offers no way to check (LinkedIn)
  2  invalid command or option (e.g. an ID prefix shorter than 6 characters)
  3  --check: the platform's answer is still uncertain; nothing was changed
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
    _add_platform_option(scan, 'Platform whose incoming folder to scan (default: linkedin).')
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
    _add_platform_option(jobs, 'Only jobs of this platform (default: every platform).')
    jobs.set_defaults(handler=_jobs)

    resolve = commands.add_parser(
        "resolve",
        help="Manually resolve a NEEDS_REVIEW job after checking the platform.",
        description=(
            "Settle a NEEDS_REVIEW job once you have checked the platform. --published keeps its content "
            "blocked from re-posting; --failed lets it be retried; files are never moved by these two. "
            "--check asks the platform instead (read-only; Instagram) and moves the files as the answer says."
        ),
        epilog=RESOLVE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    resolve.add_argument("job_id", type=_job_id_prefix, help=f"Job ID, or its first {MIN_JOB_ID_PREFIX}+ characters.")
    outcome = resolve.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--published", metavar="POST_ID", help="It was published; record the platform's post ID.")
    outcome.add_argument("--failed", action="store_true", help="It was not published; allow a retry.")
    outcome.add_argument("--check", action="store_true",
                         help="Ask the platform (read-only; never publishes). Not available for LinkedIn.")
    _add_platform_option(resolve, 'Only if the job belongs to this platform (a safety check; the job ID decides).')
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
    _add_platform_option(run, 'Platform to process (default: linkedin; with --scheduled, default: every platform).')
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
    _add_platform_option(schedule, 'Platform of the post (default: linkedin).')
    schedule.set_defaults(handler=_schedule)

    unschedule = commands.add_parser(
        "unschedule",
        help="Cancel a post's schedule. Its files stay in incoming/.",
        description="Cancel the active schedule of one post. The files are not moved; the next run processes the post.",
        epilog=UNSCHEDULE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    unschedule.add_argument("post", help="The post's name: its file names without the extension.")
    _add_platform_option(unschedule, 'Platform of the post (default: linkedin).')
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
    _add_platform_option(reconfirm, 'Platform of the post (default: linkedin).')
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
    _add_platform_option(schedules, 'Only schedules of this platform (default: every platform).')
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


def _add_platform_option(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument("--platform", type=_platform, metavar="{linkedin,instagram}", help=help_text)


def _platform(text: str) -> Platform:
    """A platform name in any case. Facebook parses, and is refused by the command (it has no integration)."""
    wanted = text.strip().casefold()
    for platform in Platform:
        if platform.value.casefold() == wanted:
            return platform
    raise argparse.ArgumentTypeError(f"unknown platform {text!r} (use linkedin or instagram)")


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
