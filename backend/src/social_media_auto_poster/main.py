"""Command-line entry point: ``python -m social_media_auto_poster.main <command>``.

- ``scan``: a read-only report of what ingestion would pick up, including duplicates.
- ``jobs``: read-only job history from ``database/app.db``.
- ``run``: process every complete post once through the workflow graph,
  recording each job in ``database/app.db``. Publishing is simulated unless
  ``--real`` is given; real mode is never a default or a setting (D36).
- ``resolve``: manually settle a NEEDS_REVIEW job as PUBLISHED or FAILED.
- ``linkedin-check``: check the LinkedIn token and setup; never creates a post.

The same operations are available to the local API (``backend/app``). They live
here as functions that return data (``scan_preview``, ``run_once``,
``resolve_job``, ``linkedin_status``); the command functions only print them and
map the result to an exit code, so the API and the CLI share one implementation.
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
from datetime import UTC, date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

from social_media_auto_poster.config import (
    DATA_DIR_VAR,
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
from social_media_auto_poster.ingestion.stability import is_file_stable
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, PublishMode, ScanResult
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
from social_media_auto_poster.services.hashing import sha256_file
from social_media_auto_poster.services.real_mode import (
    SimulatedOnly,
    archive_without_history,
    posts_only_simulated,
)
from social_media_auto_poster.services.reconcile import Recovery, reconcile_interrupted_jobs
from social_media_auto_poster.services.run_lock import RunLockHeldError, single_run_lock
from social_media_auto_poster.storage.database import SchemaTooNewError, open_app_db, open_app_db_read_only
from social_media_auto_poster.storage.jobs import JobNotFoundError, JobRecord, JobRepository

logger = logging.getLogger(__name__)

PLATFORM = Platform.LINKEDIN  # The only platform in scope.
DEFAULT_INTERVAL_S = 1.0
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5

EXIT_OK = 0
EXIT_SETUP_ERROR = 1  # Also: run --real refused before publishing anything.
EXIT_USAGE = 2  # argparse uses it too.
EXIT_JOB_PROBLEMS = 3
EXIT_LOCKED = 4

# Outcome counters reported on the "Run finished" log line, in this order.
RUN_COUNTS = (
    "simulated", "published", "failed", "needs_review", "duplicates",
    "waiting", "pending", "ambiguous", "recovered", "resumed", "errors",
)
MIN_JOB_ID_PREFIX = 6
DUPLICATE_LABEL = "DUPLICATE"
REAL_CONFIRMATION = "publish to LinkedIn"  # The API requires this text to start a real run.

# The current run's short ID, added to every log-file line by _RunIdFilter. "-" outside a run.
_RUN_ID: ContextVar[str] = ContextVar("run_id", default="-")

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
class RunReport:
    mode: PublishMode
    exit_code: int
    refusal: RunRefusal | None
    resumed: tuple[JobLine, ...]
    recoveries: tuple[Recovery, ...]
    jobs: tuple[JobLine, ...]
    scan: ScanResult | None  # None when the run stopped before scanning.
    footer: str


@dataclass(frozen=True)
class CandidateView:
    """What a read-only scan knows about one candidate post."""

    candidate: PostCandidate
    unstable: tuple[str, ...]  # "media" and/or "caption" while still changing or locked.
    errors: tuple[str, ...]  # Validation errors; empty when valid.
    duplicate: JobRecord | None  # A live job with the same content in this mode.
    duplicate_check_failed: bool  # The files could not be read to check.
    simulated: JobRecord | None  # Real mode only: this content was simulated and a real run refuses it.

    @property
    def stable(self) -> bool:
        return not self.unstable

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ScanPreview:
    folder: Path
    candidates: tuple[CandidateView, ...]
    scan: ScanResult
    interrupted_jobs: int  # Jobs in PUBLISHING: a crash left them, and the next run recovers them.
    unknown_archive: tuple[Path, ...]  # Real mode only: archive folders the job database cannot account for.


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


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``), run the command, and return its exit code."""
    args = _build_parser().parse_args(argv)
    return args.handler(args)


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
    return parser


def _add_interval_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--interval",
        type=_non_negative_float,
        default=DEFAULT_INTERVAL_S,
        help=f"Seconds to watch each file for changes (default: {DEFAULT_INTERVAL_S}; 0 checks only for locks).",
    )


# Shared checks (used by scan, run, and the API)


def unstable_files(candidate: PostCandidate, interval_s: float) -> tuple[str, ...]:
    """Which of the two files ("media", "caption") is still changing or locked. Both must be complete to post."""
    files = {"media": candidate.media_path, "caption": candidate.caption_path}
    return tuple(role for role, path in files.items() if not is_file_stable(path, interval_s))


def find_duplicate(candidate: PostCandidate, repository: JobRepository, mode: PublishMode) -> JobRecord | None:
    """The live job that already handles this content in ``mode``, if any. Raises OSError if a file cannot be read."""
    media_sha256 = sha256_file(candidate.media_path)
    caption_sha256 = sha256_file(candidate.caption_path)
    return repository.find_blocking_duplicate(
        platform=candidate.platform, mode=mode, media_sha256=media_sha256, caption_sha256=caption_sha256
    )


# scan


def scan_preview(
    settings: Settings, *, folder: Path, mode: PublishMode, interval_s: float, check_archive: bool
) -> ScanPreview:
    """Read-only report of ``folder``. Never moves, publishes, or creates anything.

    Raises FileNotFoundError if the folder does not exist. ``check_archive`` adds the
    real-mode archive check (only meaningful for the configured incoming folder).
    """
    result = scan_incoming(folder, PLATFORM)
    conn = _open_history_or_none(settings)
    try:
        repository = JobRepository(conn) if conn is not None else None
        views = tuple(_view_candidate(c, repository, mode, interval_s) for c in result.candidates)
        interrupted = len(repository.list_by_status(JobStatus.PUBLISHING)) if repository is not None else 0
        unknown: tuple[Path, ...] = ()
        if mode is PublishMode.REAL and check_archive:
            unknown = tuple(archive_without_history(settings, repository, PLATFORM))
    finally:
        if conn is not None:
            conn.close()
    return ScanPreview(folder=folder, candidates=views, scan=result, interrupted_jobs=interrupted, unknown_archive=unknown)


def _view_candidate(
    candidate: PostCandidate, repository: JobRepository | None, mode: PublishMode, interval_s: float
) -> CandidateView:
    unstable = unstable_files(candidate, interval_s)
    errors = tuple(validate_candidate(candidate).errors)
    duplicate = None
    failed = False
    simulated = None
    # Only a stable, valid post is checked for duplicates: a file still changing is not hashed.
    if repository is not None and not unstable and not errors:
        try:
            duplicate = find_duplicate(candidate, repository, mode)
        except OSError:
            failed = True
        if duplicate is None and not failed and mode is PublishMode.REAL:
            found = posts_only_simulated((candidate,), repository)
            simulated = found[0].simulated_job if found else None
    return CandidateView(candidate, unstable, errors, duplicate, failed, simulated)


def _scan(args: argparse.Namespace) -> int:
    settings = load_settings()
    mode = _mode(args)
    folder = (args.dir if args.dir is not None else settings.incoming_dir(PLATFORM)).resolve()
    with _logging_to(verbose=False, log_file=None):
        try:
            preview = scan_preview(
                settings, folder=folder, mode=mode, interval_s=args.interval, check_archive=args.dir is None
            )
        except FileNotFoundError:
            print(f"Error: incoming folder not found: {folder}", file=sys.stderr)
            print(f"Create it, or set {DATA_DIR_VAR}, or pass --dir.", file=sys.stderr)
            return EXIT_SETUP_ERROR

        if args.dir is None and data_dir_outside_base(settings):
            print(_outside_warning(settings), file=sys.stderr)
        print(f"Scanning LinkedIn incoming: {folder}" + ("   (duplicate check: real mode)" if args.real else ""))
        result = preview.scan
        if not (result.candidates or result.pending or result.ambiguous):
            print("\nNothing found.")
        else:
            _print_candidates(preview.candidates, mode)
            _print_pending_and_ambiguous(result)
        if preview.interrupted_jobs:
            print(f"\nNote: {preview.interrupted_jobs} interrupted job(s) will be recovered by the next run.")
        if mode is PublishMode.REAL and args.dir is None and preview.unknown_archive:
            print(f"\nNote: run --real would refuse: {len(preview.unknown_archive)} folder(s) in archive/, but no "
                  "real jobs in the job database.")
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


def _print_candidates(views: tuple[CandidateView, ...], mode: PublishMode) -> None:
    if not views:
        return
    pairs = [f"{v.candidate.media_path.name} + {v.candidate.caption_path.name}" for v in views]
    stability = [_stability_label(v) for v in views]
    stem_width = max(len(v.candidate.stem) for v in views)
    pair_width = max(len(p) for p in pairs)
    stability_width = max(len(s) for s in stability)
    print(f"\nCANDIDATES ({len(views)})")
    for view, pair, stable in zip(views, pairs, stability):
        if not view.valid:
            last = f"INVALID: {'; '.join(view.errors)}"
        elif view.stable:
            last = f"valid   {_duplicate_label(view, mode)}"
        else:
            last = "valid"  # Not hashed while it may still be changing.
        print(f"  {view.candidate.stem:<{stem_width}}   {pair:<{pair_width}}   {stable:<{stability_width}}   {last}")


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


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
    stamp = f"{_utc_now():%Y-%m-%dT%H:%M:%SZ}"
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
        with _logging_to(verbose=False, log_file=settings.log_file):
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


# run


def run_once(
    settings: Settings,
    mode: PublishMode,
    *,
    publish_simulated: bool = False,
    interval_s: float = DEFAULT_INTERVAL_S,
) -> RunReport:
    """One complete run: the lock, crash recovery, real-mode safeguards, and every complete post once.

    Simulated unless ``mode`` is REAL. Returns a RunReport; a refusal is data in the report, not an
    exception. Raises RunLockHeldError if another run holds the lock, and OSError if the run cannot
    be set up. Log handlers are the caller's job (the CLI and the API each attach their own).
    """
    started = time.monotonic()
    counts: Counter[str] = Counter()
    report: RunReport | None = None
    with single_run_lock(settings.lock_file), _run_scope():
        logger.info("Run started: platform=%s mode=%s", PLATFORM, mode)
        if data_dir_outside_base(settings):
            logger.warning(_outside_warning(settings).removeprefix("Warning: "))
        try:
            report = _run_locked(settings, mode, publish_simulated, interval_s, counts)
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
    if args.publish_simulated and mode is not PublishMode.REAL:
        print("Error: --publish-simulated only applies together with --real.", file=sys.stderr)
        return EXIT_USAGE
    settings = load_settings()
    try:
        with _logging_to(verbose=args.verbose, log_file=settings.log_file):
            # flush: when output is piped, stdout is buffered and stderr warnings would overtake it.
            banner = "   (REAL MODE: posts are published to LinkedIn)" if mode is PublishMode.REAL else ""
            print(f"Processing LinkedIn incoming: {settings.incoming_dir(PLATFORM)}{banner}", flush=True)
            report = run_once(settings, mode, publish_simulated=args.publish_simulated, interval_s=args.interval)
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED
    except OSError as error:
        print(f"Error: could not set up the run: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR
    _print_run_report(report, settings)
    return report.exit_code


def _run_locked(
    settings: Settings, mode: PublishMode, publish_simulated: bool, interval_s: float, counts: Counter[str]
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
        # Real-mode checks come before anything changes, so a refused run leaves everything as it was.
        publisher = _publisher_for(mode, settings, repository, stack)
        if isinstance(publisher, RunRefusal):
            return _refused(mode, publisher)
        try:
            # Resume first: it settles what checkpoints can prove; reconciliation handles the rest.
            resumed = resume_interrupted_jobs(checkpointer, repository, settings)
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
        )
        return _process(context, publish_simulated, tuple(resumed_lines), recoveries, earlier_problems, counts)


def _refused(mode: PublishMode, refusal: RunRefusal) -> RunReport:
    return RunReport(
        mode=mode, exit_code=EXIT_SETUP_ERROR, refusal=refusal, resumed=(), recoveries=(), jobs=(), scan=None, footer=""
    )


class RealPublishingUnavailable(Exception):
    """Real publishing cannot start; the message says why."""


def _today() -> date:
    return date.today()


def _outside_warning(settings: Settings) -> str:
    return (
        f"Warning: the data folder is outside backend\\: {settings.data_dir} "
        f"(check {DATA_DIR_VAR} in backend\\.env)"
    )


def _linkedin_client(access_token: str) -> LinkedInClient:
    return LinkedInClient(access_token)


def _real_publisher(settings: Settings) -> Publisher:
    """The LinkedIn publisher, once the token in backend/.env is present, not expired, and accepted.

    The token is checked with LinkedIn (``/v2/userinfo``) here, before anything changes.
    """
    today = _today()
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

    if context.mode is PublishMode.REAL and not publish_simulated:
        simulated_only = posts_only_simulated(result.candidates, context.repository)
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
            )

    lines = tuple(_run_one(candidate, context) for candidate in result.candidates)
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
    )


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


def _run_one(candidate: PostCandidate, context: WorkflowContext) -> JobLine:
    try:
        final = run_job(candidate, context)
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

    if final["retry_later"]:
        return JobLine(stem, "WAITING (still copying)", "will retry on next run", (), problem=False, outcome="waiting")
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


def _print_run_report(report: RunReport, settings: Settings) -> None:
    """The terminal view of a run. Everything it shows is already in ``report``."""
    if report.resumed:
        _print_jobs(list(report.resumed), title="RESUMED", note="jobs finished after an earlier crash")
    if report.recoveries:
        _print_recoveries(list(report.recoveries))
    if report.refusal is not None:
        if report.refusal.text:
            stream = sys.stdout if report.refusal.kind == "simulated_only" else sys.stderr
            print(report.refusal.text, file=stream)
        return
    if report.scan is None:  # Setup error: the log says what happened.
        return
    if report.jobs:
        _print_jobs(list(report.jobs))
    else:
        print("\nNothing to process.")
    _print_pending_and_ambiguous(report.scan)
    print(report.footer)
    if any(line.label == DUPLICATE_LABEL for line in report.jobs):
        print("Duplicates stay in incoming/; delete them when you no longer need them.")
    print(f"Log: {settings.log_file}")


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
    today = today or _today()
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
        credentials = load_linkedin_credentials(settings.base_dir, today=_today())
    except CredentialsError as error:
        print(f"  PROBLEM  {error}")
        print("\nNot ready for run --real: fix the problem above.")
        return EXIT_SETUP_ERROR
    with _logging_to(verbose=False, log_file=None), closing(_linkedin_client(credentials.access_token)) as client:
        report = run_check(credentials, client, today=_today(), upload_test=args.upload_test)
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


@contextmanager
def _logging_to(*, verbose: bool, log_file: Path | None, console: bool = True) -> Generator[None, None, None]:
    """Attach log handlers for one command or request: the terminal (``console``) and an optional rotating file.

    Handlers are removed and closed afterwards, so Windows never keeps the log
    file locked once the command returns. Log lines carry IDs, stems, platform,
    status, and error messages only, never caption text.
    """
    handlers: list[logging.Handler] = []
    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(logging.INFO if verbose else logging.WARNING)
        stream_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        handlers.append(stream_handler)

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
