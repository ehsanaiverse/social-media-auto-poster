"""One handler per command: call the operation, print its result, and return the exit code."""

import argparse
import sqlite3
import sys
from contextlib import closing

from social_media_auto_poster.cli.output import (
    _print_candidates,
    _print_job_table,
    _print_missing_schedules,
    _print_pending_and_ambiguous,
    _print_resolve_next_steps,
    _print_run_report,
    _print_schedule_table,
)
from social_media_auto_poster.config import DATA_DIR_VAR, data_dir_outside_base, load_settings
from social_media_auto_poster.models import JobStatus, PublishMode, ScheduleStatus
from social_media_auto_poster.operations import clock, linkedin as linkedin_ops
from social_media_auto_poster.operations.common import (
    EXIT_JOB_PROBLEMS,
    EXIT_LOCKED,
    EXIT_OK,
    EXIT_SETUP_ERROR,
    EXIT_USAGE,
    PLATFORM,
    _local_time,
    outside_warning,
)
from social_media_auto_poster.operations.logs import logging_to
from social_media_auto_poster.operations import instagram as instagram_ops
from social_media_auto_poster.operations.platforms import UnsupportedPlatform, adapter_for, real_unavailable_text
from social_media_auto_poster.operations.resolve import ResolveError, check_job, resolve_job
from social_media_auto_poster.operations.run import run_once
from social_media_auto_poster.operations.scan import scan_preview
from social_media_auto_poster.operations.schedules import (
    list_schedules,
    reconfirm_post,
    schedule_post,
    unschedule_post,
)
from social_media_auto_poster.platforms.linkedin.check import run_check
from social_media_auto_poster.platforms.rules import display_name
from social_media_auto_poster.platforms.linkedin.credentials import CredentialsError, load_linkedin_credentials
from social_media_auto_poster.services.run_lock import RunLockHeldError
from social_media_auto_poster.services.scheduling import ScheduleError
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobRepository


LOCKED_TEXT = "Another run is in progress; try again when it has finished."


def _platform_refused(platform, *, real: bool = False) -> bool:
    """Print why ``--platform`` cannot be used here (exit 1), before any lock, log or database. None: fine."""
    if platform is None:
        return False
    try:
        adapter_for(platform)
    except UnsupportedPlatform as error:
        print(f"Error: {error}.", file=sys.stderr)
        return True
    refused = real_unavailable_text(platform) if real else None
    if refused:
        print(f"Error: {refused}.", file=sys.stderr)
        return True
    return False


def _scan(args: argparse.Namespace) -> int:
    if _platform_refused(args.platform, real=args.real):
        return EXIT_SETUP_ERROR
    platform = args.platform or PLATFORM
    settings = load_settings()
    mode = _mode(args)
    folder = (args.dir if args.dir is not None else settings.incoming_dir(platform)).resolve()
    with logging_to(verbose=False, log_file=None):
        try:
            preview = scan_preview(
                settings, folder=folder, mode=mode, interval_s=args.interval, check_archive=args.dir is None,
                platform=platform,
            )
        except FileNotFoundError:
            print(f"Error: incoming folder not found: {folder}", file=sys.stderr)
            print(f"Create it, or set {DATA_DIR_VAR}, or pass --dir.", file=sys.stderr)
            return EXIT_SETUP_ERROR

        if args.dir is None and data_dir_outside_base(settings):
            print(outside_warning(settings), file=sys.stderr)
        print(f"Scanning {display_name(platform)} incoming: {folder}" + ("   (duplicate check: real mode)" if args.real else ""))
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


def _jobs(args: argparse.Namespace) -> int:
    if _platform_refused(args.platform):
        return EXIT_SETUP_ERROR
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
            return _show_job(repository, args.job_id.strip().lower(), args.platform)
        status = JobStatus(args.status) if args.status else None
        records = repository.list_recent(args.last, status=status, platform=args.platform)
        if not records:
            on = f" on {display_name(args.platform)}" if args.platform else ""
            print(f"No jobs with status {status}{on}." if status else f"No jobs yet{on}.")
            return EXIT_OK
        _print_job_table(records)
        return EXIT_OK
    except sqlite3.Error as error:
        print(f"Error: could not read the job database: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    finally:
        conn.close()


def _show_job(repository: JobRepository, prefix: str, platform=None) -> int:
    matches = [r for r in repository.find_by_id_prefix(prefix) if platform is None or r.platform is platform]
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


def _resolve(args: argparse.Namespace) -> int:
    if _platform_refused(args.platform):
        return EXIT_SETUP_ERROR
    settings = load_settings()
    if args.check:
        return _resolve_check(args.job_id, settings, args.platform)
    published = args.published
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            result = resolve_job(settings, args.job_id, published_post_id=published, platform=args.platform)
    except ResolveError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        for line in error.details:
            print(line, file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print(LOCKED_TEXT, file=sys.stderr)
        return EXIT_LOCKED

    name = f"job {result.job.job_id[:8]} ({result.job.stem})"
    print(f"Resolved {name} as {result.status}.")
    print(f"Note recorded: {result.note}")
    _print_resolve_next_steps(result.job, result.status, settings)
    return EXIT_OK


def _resolve_check(job_id: str, settings, platform=None) -> int:
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            result = check_job(settings, job_id, platform=platform)
    except ResolveError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        for line in error.details:
            print(line, file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print(LOCKED_TEXT, file=sys.stderr)
        return EXIT_LOCKED

    name = f"job {result.job.job_id[:8]} ({result.job.stem})"
    print(f"{display_name(result.job.platform)} says: {result.reason}")
    if result.status is JobStatus.NEEDS_REVIEW:
        print(f"{name} stays NEEDS_REVIEW; nothing was changed. Check again later, or use --published/--failed.")
        return EXIT_JOB_PROBLEMS
    post = f" (post ID {result.post_id})" if result.post_id else (" (no post ID: the post could not be identified)"
                                                              if result.status is JobStatus.PUBLISHED else "")
    print(f"Resolved {name} as {result.status}{post}.")
    for note in result.notes:
        print(f"  {note}")
    if result.status is JobStatus.FAILED:
        print("It was never published, so its content can be posted again: move the files back to incoming/.")
    return EXIT_OK


def _schedule(args: argparse.Namespace) -> int:
    if _platform_refused(args.platform, real=args.real):
        return EXIT_SETUP_ERROR
    settings = load_settings()
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            result = schedule_post(settings, args.post, args.at, real=args.real, interval_s=args.interval,
                                   platform=args.platform or PLATFORM)
    except ScheduleError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print(LOCKED_TEXT, file=sys.stderr)
        return EXIT_LOCKED

    record = result.schedule
    kind = (f"REAL: to be published to {display_name(record.platform)}" if record.mode is PublishMode.REAL
            else f"simulated: nothing will be sent to {display_name(record.platform)}")
    print(f"Scheduled '{record.stem}' for {result.local_time} ({record.publish_at:%Y-%m-%d %H:%M} UTC), {kind}.")
    print(f"Schedule ID: {record.schedule_id[:8]}")
    print("Keep both files unchanged in incoming/ until then; a changed file makes the schedule INVALID.")
    print("A scheduled run publishes it at or after that time: run --scheduled (add --real for a real post), "
          "for example every 15 minutes from Windows Task Scheduler. Until then every other run skips it.")
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return EXIT_OK


def _unschedule(args: argparse.Namespace) -> int:
    if _platform_refused(args.platform):
        return EXIT_SETUP_ERROR
    settings = load_settings()
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            cancelled = unschedule_post(settings, args.post, platform=args.platform or PLATFORM)
    except ScheduleError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print(LOCKED_TEXT, file=sys.stderr)
        return EXIT_LOCKED
    when = _local_time(cancelled.publish_at, settings.timezone)
    print(f"Cancelled the schedule of '{cancelled.stem}' (was {when}, {cancelled.mode}).")
    print("Its files stay in incoming/; the next run processes it like any other post "
          "(run --real would publish it).")
    return EXIT_OK


def _schedules(args: argparse.Namespace) -> int:
    if _platform_refused(args.platform):
        return EXIT_SETUP_ERROR
    settings = load_settings()
    status = ScheduleStatus(args.status) if args.status else None
    try:
        listing = list_schedules(settings, include_all=args.all, status=status, limit=args.last,
                                 platform=args.platform)
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


def _reconfirm(args: argparse.Namespace) -> int:
    if _platform_refused(args.platform, real=args.real):
        return EXIT_SETUP_ERROR
    settings = load_settings()
    try:
        with logging_to(verbose=False, log_file=settings.log_file):
            result = reconfirm_post(settings, args.post, real=args.real, at=args.at, interval_s=args.interval,
                                    platform=args.platform or PLATFORM)
    except ScheduleError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    except RunLockHeldError:
        print(LOCKED_TEXT, file=sys.stderr)
        return EXIT_LOCKED

    new, old = result.new, result.old
    kind = (f"REAL: to be published to {display_name(new.platform)}" if new.mode is PublishMode.REAL
            else f"simulated: nothing will be sent to {display_name(new.platform)}")
    old_now = "now CANCELLED" if old.status is ScheduleStatus.SCHEDULED else f"which stays {old.status}"
    print(f"Re-confirmed '{new.stem}' for {result.local_time} ({new.publish_at:%Y-%m-%d %H:%M} UTC), {kind}.")
    print(f"Schedule ID: {new.schedule_id[:8]} (replaces {old.schedule_id[:8]}, {old_now}).")
    print("The schedule now covers the files as they are; keep them unchanged until then.")
    print("A scheduled run publishes it at or after that time: run --scheduled (add --real for a real post), "
          "for example every 15 minutes from Windows Task Scheduler. Until then every other run skips it.")
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    mode = _mode(args)
    if args.scheduled and args.publish_simulated:
        print("Error: --scheduled cannot be combined with --publish-simulated: a scheduled run publishes only "
              "scheduled posts, each confirmed when it was scheduled.", file=sys.stderr)
        return EXIT_USAGE
    if args.publish_simulated and mode is not PublishMode.REAL:
        print("Error: --publish-simulated only applies together with --real.", file=sys.stderr)
        return EXIT_USAGE
    if _platform_refused(args.platform, real=mode is PublishMode.REAL):
        return EXIT_SETUP_ERROR
    shown = args.platform or PLATFORM  # Without --platform: as before (a scheduled run covers every platform).
    settings = load_settings()
    try:
        with logging_to(verbose=args.verbose, log_file=settings.log_file):
            # flush: when output is piped, stdout is buffered and stderr warnings would overtake it.
            banner = f"   (REAL MODE: posts are published to {display_name(shown)})" if mode is PublishMode.REAL else ""
            if args.scheduled:
                banner += "   (scheduled run: only scheduled posts that are due)"
            print(f"Processing {display_name(shown)} incoming: {settings.incoming_dir(shown)}{banner}", flush=True)
            report = run_once(settings, mode, publish_simulated=args.publish_simulated, interval_s=args.interval,
                              scheduled=args.scheduled, platform=args.platform)
    except RunLockHeldError:
        print(LOCKED_TEXT, file=sys.stderr)
        return EXIT_LOCKED
    except OSError as error:
        print(f"Error: could not set up the run: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    _print_run_report(report, settings)
    return report.exit_code


def _mode(args: argparse.Namespace) -> PublishMode:
    """Real only when --real was given on this command line (D36)."""
    return PublishMode.REAL if args.real else PublishMode.SIMULATED


def _instagram_check(args: argparse.Namespace) -> int:
    settings = load_settings()
    print("Instagram check (nothing is ever published)")
    with logging_to(verbose=False, log_file=None):
        report = instagram_ops.instagram_check(settings)
    print("\n".join(report.lines))
    print("\nReady for run --real --platform instagram." if report.ok else "\nNot ready for real Instagram: fix the problems above.")
    return EXIT_OK if report.ok else EXIT_SETUP_ERROR


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
