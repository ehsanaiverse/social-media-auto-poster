"""Command-line entry point: ``python -m social_media_auto_poster.main <command>``.

- ``scan``: a read-only report of what ingestion would pick up, including duplicates.
- ``jobs``: read-only job history from ``database/app.db``.
- ``run``: process every complete post once through the workflow graph,
  recording each job in ``database/app.db``. Publishing is always simulated
  until Milestone 4.
- ``resolve``: manually settle a NEEDS_REVIEW job as PUBLISHED or FAILED.
"""

import argparse
import logging
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Generator
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from social_media_auto_poster.config import DATA_DIR_VAR, Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.graph.checkpoints import open_checkpointer
from social_media_auto_poster.graph.state import JobState, WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.ingestion.stability import is_file_stable
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode, ScanResult
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.reconcile import Recovery, reconcile_interrupted_jobs
from social_media_auto_poster.services.run_lock import RunLockHeldError, single_run_lock
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobNotFoundError, JobRecord, JobRepository

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 1.0
# The publish mode of every run until Milestone 4. scan uses it too, so its duplicate check matches run's.
RUN_MODE = PublishMode.SIMULATED
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5

EXIT_OK = 0
EXIT_SETUP_ERROR = 1
# 2 is argparse's usage error.
EXIT_JOB_PROBLEMS = 3
EXIT_LOCKED = 4

# Outcome counters reported on the "Run finished" log line, in this order.
RUN_COUNTS = (
    "simulated", "published", "failed", "needs_review", "duplicates",
    "waiting", "pending", "ambiguous", "recovered", "errors",
)
MIN_JOB_ID_PREFIX = 6

# The current run's short ID, added to every log-file line by _RunIdFilter. "-" outside a run.
_RUN_ID: ContextVar[str] = ContextVar("run_id", default="-")

RUN_EPILOG = """\
exit codes:
  0  run completed; every job was simulated, a duplicate, or waiting, or there was nothing to do
  1  setup error (data folder, job database, or crash recovery could not complete)
  2  invalid command or option
  3  run completed, but at least one job ended FAILED, NEEDS_REVIEW, or ERROR,
     or an interrupted job was recovered as NEEDS_REVIEW
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


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``), run the command, and return its exit code."""
    args = _build_parser().parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m social_media_auto_poster.main",
        description="Folder-driven social media auto-poster. LinkedIn only; publishing is simulated.",
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
        help="Process every complete post once. Publishing is simulated.",
        description=(
            "Run each complete post in the configured LinkedIn incoming folder through the workflow. "
            "Invalid or failed posts move to failed/ with an error report; simulated posts stay in incoming/."
        ),
        epilog=RUN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_interval_option(run)
    run.add_argument("--verbose", action="store_true", help="Also show INFO log lines in the terminal.")
    run.set_defaults(handler=_run)
    return parser


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
    folder = (args.dir if args.dir is not None else settings.incoming_dir(Platform.LINKEDIN)).resolve()
    with _logging_to(verbose=False, log_file=None):
        try:
            result = scan_incoming(folder, Platform.LINKEDIN)
        except FileNotFoundError:
            print(f"Error: incoming folder not found: {folder}", file=sys.stderr)
            print(f"Create it, or set {DATA_DIR_VAR}, or pass --dir.", file=sys.stderr)
            return EXIT_SETUP_ERROR

        conn = _open_history_or_none(settings)
        try:
            repository = JobRepository(conn) if conn is not None else None
            print(f"Scanning LinkedIn incoming: {folder}")
            if not (result.candidates or result.pending or result.ambiguous):
                print("\nNothing found.")
            else:
                _print_candidates(result, args.interval, repository)
                _print_pending_and_ambiguous(result)
            if repository is not None:
                interrupted = len(repository.list_by_status(JobStatus.PUBLISHING))
                if interrupted:
                    print(f"\nNote: {interrupted} interrupted job(s) will be recovered by the next run.")
        finally:
            if conn is not None:
                conn.close()
    print("\nDry run: nothing was moved or published.")
    return EXIT_OK


def _open_history_or_none(settings: Settings) -> sqlite3.Connection | None:
    """The job database, read-only, or None if it does not exist yet or cannot be read (scan still works)."""
    try:
        return open_app_db_read_only(settings.app_db_path)
    except FileNotFoundError:
        return None
    except (SchemaTooNewError, sqlite3.Error) as error:
        logger.warning("Duplicate check unavailable; could not read the job database: %s", error)
        return None


def _print_candidates(result: ScanResult, interval_s: float, repository: JobRepository | None) -> None:
    if not result.candidates:
        return
    pairs = [f"{c.media_path.name} + {c.caption_path.name}" for c in result.candidates]
    stability = [_stability_label(c, interval_s) for c in result.candidates]
    validity = [validate_candidate(c) for c in result.candidates]
    stem_width = max(len(c.stem) for c in result.candidates)
    pair_width = max(len(p) for p in pairs)
    stability_width = max(len(s) for s in stability)
    print(f"\nCANDIDATES ({len(result.candidates)})")
    for candidate, pair, stable, valid in zip(result.candidates, pairs, stability, validity):
        if not valid.is_valid:
            last = f"INVALID: {'; '.join(valid.errors)}"
        elif stable == "stable":
            last = f"valid   {_duplicate_label(candidate, repository)}"
        else:
            last = "valid"  # Not hashed while it may still be changing.
        print(f"  {candidate.stem:<{stem_width}}   {pair:<{pair_width}}   {stable:<{stability_width}}   {last}")


def _duplicate_label(candidate: PostCandidate, repository: JobRepository | None) -> str:
    """Whether ``run`` would skip this post as a duplicate. Same check as the graph, read-only."""
    if repository is None:
        return "new"
    try:
        media_sha256 = sha256_file(candidate.media_path)
        caption_sha256 = sha256_file(candidate.caption_path)
    except OSError:
        return "could not read files to check for duplicates"
    match = repository.find_blocking_duplicate(
        platform=candidate.platform, mode=RUN_MODE, media_sha256=media_sha256, caption_sha256=caption_sha256
    )
    return f"DUPLICATE {_describe_original(match)}" if match else "new"


def _stability_label(candidate: PostCandidate, interval_s: float) -> str:
    # Both files must be complete before a post can be processed.
    files = {"media": candidate.media_path, "caption": candidate.caption_path}
    unstable = [role for role, path in files.items() if not is_file_stable(path, interval_s)]
    if not unstable:
        return "stable"
    return f"NOT STABLE ({' and '.join(unstable)} changing or locked)"


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


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _resolve(args: argparse.Namespace) -> int:
    if args.published is not None and not args.published.strip():
        print("Error: --published needs the platform's post ID (it must not be empty).", file=sys.stderr)
        return EXIT_SETUP_ERROR
    settings = load_settings()
    try:
        # The same lock as run: resolve and run never change the database at the same time.
        with single_run_lock(settings.lock_file), _logging_to(verbose=False, log_file=settings.log_file), _run_scope():
            if not settings.app_db_path.is_file():
                print(f"Error: no job database at {settings.app_db_path}; nothing to resolve.", file=sys.stderr)
                return EXIT_SETUP_ERROR
            try:
                conn = open_app_db(settings.app_db_path)
            except (SchemaTooNewError, sqlite3.Error) as error:
                print(f"Error: could not open the job database: {error}", file=sys.stderr)
                return EXIT_SETUP_ERROR
            try:
                return _resolve_job(JobRepository(conn), settings, args)
            finally:
                conn.close()
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED


def _resolve_job(repository: JobRepository, settings: Settings, args: argparse.Namespace) -> int:
    matches = repository.find_by_id_prefix(args.job_id)
    if not matches:
        print(f"Error: no job ID starts with '{args.job_id}'. Nothing was changed.", file=sys.stderr)
        return EXIT_SETUP_ERROR
    if len(matches) > 1:
        print(f"Error: '{args.job_id}' matches {len(matches)} jobs; use a longer prefix:", file=sys.stderr)
        for r in matches:
            print(f"  {r.job_id}  {r.stem}  {r.status}", file=sys.stderr)
        return EXIT_SETUP_ERROR

    [job] = matches
    name = f"job {job.job_id[:8]} ({job.stem})"
    if job.status is not JobStatus.NEEDS_REVIEW:
        print(
            f"Error: {name} is {job.status}; only NEEDS_REVIEW jobs can be resolved. Nothing was changed.",
            file=sys.stderr,
        )
        return EXIT_SETUP_ERROR
    published = args.published is not None
    if published and job.mode is PublishMode.SIMULATED:
        print(
            f"Error: {name} is a simulated job; nothing was sent, so it cannot have been published. "
            "Resolve it with --failed. Nothing was changed.",
            file=sys.stderr,
        )
        return EXIT_SETUP_ERROR

    status = JobStatus.PUBLISHED if published else JobStatus.FAILED
    post_id = args.published.strip() if published else None
    stamp = f"{_utc_now():%Y-%m-%dT%H:%M:%SZ}"
    note = (
        f"resolved manually as PUBLISHED (post ID {post_id}) at {stamp}"
        if published
        else f"resolved manually as FAILED at {stamp}"
    )
    try:
        repository.resolve_needs_review(job.job_id, status=status, note=note, platform_post_id=post_id)
    except JobNotFoundError:  # Cannot happen while we hold the lock; refuse rather than guess.
        print(f"Error: {name} is no longer NEEDS_REVIEW. Nothing was changed.", file=sys.stderr)
        return EXIT_SETUP_ERROR
    logger.info(
        "Job resolved manually: job_id=%s platform=%s mode=%s stem=%s status=%s post_id=%s",
        job.job_id, job.platform, job.mode, job.stem, status, post_id,
    )

    print(f"Resolved {name} as {status}.")
    print(f"Note recorded: {note}")
    _print_resolve_next_steps(job, status, settings)
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


# run


@dataclass(frozen=True)
class _JobLine:
    """One job's line in the run summary."""

    stem: str
    label: str
    note: str
    details: tuple[str, ...]
    problem: bool  # Counts toward exit code 3.
    outcome: str  # One of RUN_COUNTS, for the "Run finished" log line.


def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        with (
            single_run_lock(settings.lock_file),
            _logging_to(verbose=args.verbose, log_file=settings.log_file),
            _run_scope(),
        ):
            started = time.monotonic()
            counts: Counter[str] = Counter()
            exit_code = EXIT_SETUP_ERROR
            logger.info("Run started: platform=%s mode=%s", Platform.LINKEDIN, RUN_MODE)
            # flush: when output is piped, stdout is buffered and stderr warnings would overtake it.
            print(f"Processing LinkedIn incoming: {settings.incoming_dir(Platform.LINKEDIN)}", flush=True)
            try:
                exit_code = _run_locked(settings, args.interval, counts)
                return exit_code
            finally:
                logger.info(
                    "Run finished: %s exit_code=%d duration_s=%.2f",
                    " ".join(f"{key}={counts[key]}" for key in RUN_COUNTS),
                    exit_code,
                    time.monotonic() - started,
                )
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED
    except OSError as error:
        print(f"Error: could not set up the run: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR


def _run_locked(settings: Settings, interval_s: float, counts: Counter[str]) -> int:
    """Everything a run does while holding the lock, after "Run started" is logged."""
    ensure_data_dirs(settings)
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(closing(open_app_db(settings.app_db_path)))
        except (SchemaTooNewError, sqlite3.Error) as error:
            logger.error("Could not open the job database %s: %s", settings.app_db_path, error)
            return EXIT_SETUP_ERROR
        try:
            checkpointer = stack.enter_context(
                open_checkpointer(settings.checkpoint_db_path, app_db_path=settings.app_db_path)
            )
        except (sqlite3.Error, ValueError) as error:
            # Fail closed: without checkpoints, a crash could not be resumed safely.
            logger.error("Could not open the checkpoint store %s: %s", settings.checkpoint_db_path, error)
            return EXIT_SETUP_ERROR
        repository = JobRepository(conn)
        try:
            recoveries = reconcile_interrupted_jobs(repository, settings)
        except sqlite3.Error:
            # Never process on top of an unknown state.
            logger.exception("Could not recover interrupted jobs; nothing was processed")
            return EXIT_SETUP_ERROR
        counts["recovered"] = len(recoveries)
        if recoveries:
            _print_recoveries(recoveries)
        return _process(settings, repository, checkpointer, interval_s, recoveries, counts)


def _process(
    settings: Settings,
    repository: JobRepository,
    checkpointer: BaseCheckpointSaver,
    interval_s: float,
    recoveries: list[Recovery],
    counts: Counter[str],
) -> int:
    platform = Platform.LINKEDIN
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

    # Until Milestone 4 the publisher is always the simulator. There is no real publisher to choose.
    context = WorkflowContext(
        publisher=Simulator(),
        mode=RUN_MODE,
        repository=repository,
        settings=settings,
        stability_interval_s=interval_s,
        checkpointer=checkpointer,
    )
    lines = [_run_one(candidate, context) for candidate in result.candidates]
    counts.update(line.outcome for line in lines)

    if lines:
        _print_jobs(lines)
    else:
        print("\nNothing to process.")
    _print_pending_and_ambiguous(result)

    print("\nSimulation mode: nothing was sent to LinkedIn.")
    if any(line.label == DUPLICATE_LABEL for line in lines):
        print("Duplicates stay in incoming/; delete them when you no longer need them.")
    print(f"Log: {settings.log_file}")
    needs_human = any(r.status is JobStatus.NEEDS_REVIEW for r in recoveries)
    return EXIT_JOB_PROBLEMS if needs_human or any(line.problem for line in lines) else EXIT_OK


def _print_recoveries(recoveries: list[Recovery]) -> None:
    stem_width = max(len(r.stem) for r in recoveries)
    status_width = max(len(r.status) for r in recoveries)
    print(f"\nRECOVERED ({len(recoveries)}) - jobs interrupted by an earlier crash")
    for r in recoveries:
        first, *rest = r.notes
        print(f"  {r.stem:<{stem_width}}   {r.status:<{status_width}}   ({r.mode}) {first}")
        for note in rest:
            print(f"  {'':<{stem_width}}   {'':<{status_width}}   - {note}")


DUPLICATE_LABEL = "DUPLICATE"


def _run_one(candidate: PostCandidate, context: WorkflowContext) -> _JobLine:
    try:
        final = run_job(candidate, context)
    except Exception:  # A bug or a database failure, not a publish outcome. Keep going.
        logger.exception(
            "Unexpected error; job abandoned: platform=%s mode=%s stem=%s", candidate.platform, context.mode, candidate.stem
        )
        return _JobLine(candidate.stem, "ERROR (unexpected)", "see the log for details", (), problem=True, outcome="errors")
    return _describe(final, context)


def _describe(final: JobState, context: WorkflowContext) -> _JobLine:
    stem, status, errors = final["stem"], final["status"], tuple(final["errors"])
    destination = _relative_to_data(final["destination"], context.settings)

    if final["retry_later"]:
        return _JobLine(stem, "WAITING (still copying)", "will retry on next run", (), problem=False, outcome="waiting")
    if final["duplicate_of"]:
        note = _duplicate_note(final["duplicate_of"], context)
        return _JobLine(stem, DUPLICATE_LABEL, note, (), problem=False, outcome="duplicates")
    if status == JobStatus.SIMULATED:
        note = "files stay in incoming/"
        return _JobLine(stem, "SIMULATED (not published)", note, errors, problem=False, outcome="simulated")
    if status == JobStatus.PUBLISHED:
        note = f"archived to {destination}" if destination else "files left in incoming/"
        return _JobLine(stem, status, note, errors, problem=bool(errors), outcome="published")
    note = f"moved to {destination}" if destination else "files left in incoming/"
    outcome = "needs_review" if status == JobStatus.NEEDS_REVIEW else "failed"
    return _JobLine(stem, status, note, errors, problem=True, outcome=outcome)


def _duplicate_note(job_id: str, context: WorkflowContext) -> str:
    original = context.repository.get(job_id)
    if original is None:
        return "of an existing job; files left in incoming/"
    return f"{_describe_original(original)}; files left in incoming/"


def _describe_original(original: JobRecord) -> str:
    """How duplicates refer to the job that already handles their content (shared by run and scan)."""
    return f"of job {original.job_id[:8]} ({original.status}, {original.created_at:%Y-%m-%d})"


def _relative_to_data(destination: str | None, settings: Settings) -> str | None:
    if destination is None:
        return None
    try:
        return str(Path(destination).relative_to(settings.data_dir))
    except ValueError:
        return destination


def _print_jobs(lines: list[_JobLine]) -> None:
    stem_width = max(len(line.stem) for line in lines)
    label_width = max(len(line.label) for line in lines)
    print(f"\nJOBS ({len(lines)})")
    for line in lines:
        print(f"  {line.stem:<{stem_width}}   {line.label:<{label_width}}   {line.note}")
        for detail in line.details:
            print(f"  {'':<{stem_width}}     - {detail}")


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


@contextmanager
def _logging_to(*, verbose: bool, log_file: Path | None) -> Generator[None, None, None]:
    """Attach console (and optionally rotating file) log handlers for one command.

    Handlers are removed and closed afterwards, so Windows never keeps the log
    file locked once the command returns. Log lines carry IDs, stems, platform,
    status, and error messages only, never caption text.
    """
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handlers: list[logging.Handler] = [console]

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.INFO)
        file_handler.addFilter(_RunIdFilter())
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s run_id=%(run_id)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%SZ"
        )
        formatter.converter = time.gmtime  # UTC, matching archive folder names and error reports.
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)
    for handler in handlers:
        root.addHandler(handler)
    try:
        yield
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)


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


class _RunIdFilter(logging.Filter):
    """Adds the current run's ID to every record (log file only; terminal lines stay short)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _RUN_ID.get()
        return True


@contextmanager
def _run_scope() -> Generator[str, None, None]:
    """Give everything logged inside the block (including graph nodes) a new short run ID."""
    token = _RUN_ID.set(uuid4().hex[:8])
    try:
        yield _RUN_ID.get()
    finally:
        _RUN_ID.reset(token)


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
