"""Command-line entry point: ``python -m social_media_auto_poster.main <command>``.

- ``scan``: a read-only report of what ingestion would pick up.
- ``run``: process every complete post once through the workflow graph.
  Publishing is always simulated in Milestone 1.
"""

import argparse
import logging
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from social_media_auto_poster.config import DATA_DIR_VAR, Settings, ensure_data_dirs, load_settings
from social_media_auto_poster.graph.state import JobState, WorkflowContext
from social_media_auto_poster.graph.workflow import run_job
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.ingestion.stability import is_file_stable
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import JobStatus, Platform, PostCandidate, ScanResult
from social_media_auto_poster.platforms.simulator import Simulator
from social_media_auto_poster.services.run_lock import RunLockHeldError, single_run_lock

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 1.0
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5

EXIT_OK = 0
EXIT_SETUP_ERROR = 1
# 2 is argparse's usage error.
EXIT_JOB_PROBLEMS = 3
EXIT_LOCKED = 4

RUN_EPILOG = """\
exit codes:
  0  run completed; every job was simulated or is waiting, or there was nothing to do
  1  setup error (for example, the data folder could not be created)
  2  invalid command or option
  3  run completed, but at least one job ended FAILED, NEEDS_REVIEW, or ERROR
  4  another run is in progress
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
    folder = args.dir if args.dir is not None else load_settings().incoming_dir(Platform.LINKEDIN)
    folder = folder.resolve()
    with _logging_to(verbose=False, log_file=None):
        try:
            result = scan_incoming(folder, Platform.LINKEDIN)
        except FileNotFoundError:
            print(f"Error: incoming folder not found: {folder}", file=sys.stderr)
            print(f"Create it, or set {DATA_DIR_VAR}, or pass --dir.", file=sys.stderr)
            return EXIT_SETUP_ERROR

        print(f"Scanning LinkedIn incoming: {folder}")
        if not (result.candidates or result.pending or result.ambiguous):
            print("\nNothing found.")
        else:
            _print_candidates(result, args.interval)
            _print_pending_and_ambiguous(result)
    print("\nDry run: nothing was moved or published.")
    return EXIT_OK


def _print_candidates(result: ScanResult, interval_s: float) -> None:
    if not result.candidates:
        return
    pairs = [f"{c.media_path.name} + {c.caption_path.name}" for c in result.candidates]
    stability = [_stability_label(c, interval_s) for c in result.candidates]
    validity = [_validation_label(c) for c in result.candidates]
    stem_width = max(len(c.stem) for c in result.candidates)
    pair_width = max(len(p) for p in pairs)
    stability_width = max(len(s) for s in stability)
    print(f"\nCANDIDATES ({len(result.candidates)})")
    for candidate, pair, stable, valid in zip(result.candidates, pairs, stability, validity):
        print(f"  {candidate.stem:<{stem_width}}   {pair:<{pair_width}}   {stable:<{stability_width}}   {valid}")


def _stability_label(candidate: PostCandidate, interval_s: float) -> str:
    # Both files must be complete before a post can be processed.
    files = {"media": candidate.media_path, "caption": candidate.caption_path}
    unstable = [role for role, path in files.items() if not is_file_stable(path, interval_s)]
    if not unstable:
        return "stable"
    return f"NOT STABLE ({' and '.join(unstable)} changing or locked)"


def _validation_label(candidate: PostCandidate) -> str:
    result = validate_candidate(candidate)
    return "valid" if result.is_valid else f"INVALID: {'; '.join(result.errors)}"


# run


@dataclass(frozen=True)
class _JobLine:
    """One job's line in the run summary."""

    stem: str
    label: str
    note: str
    details: tuple[str, ...]
    problem: bool  # Counts toward exit code 3.


def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        with single_run_lock(settings.lock_file), _logging_to(verbose=args.verbose, log_file=settings.log_file):
            return _process(settings, args.interval)
    except RunLockHeldError:
        print("Another run is in progress; try again when it has finished.", file=sys.stderr)
        return EXIT_LOCKED
    except OSError as error:
        print(f"Error: could not set up the run: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR


def _process(settings: Settings, interval_s: float) -> int:
    ensure_data_dirs(settings)
    platform = Platform.LINKEDIN
    incoming = settings.incoming_dir(platform)
    result = scan_incoming(incoming, platform)
    logger.info(
        "Run started: platform=%s candidates=%d pending=%d ambiguous=%d",
        platform,
        len(result.candidates),
        len(result.pending),
        len(result.ambiguous),
    )

    # Milestone 1: the publisher is always the simulator. There is no real publisher to choose.
    context = WorkflowContext(publisher=Simulator(), settings=settings, stability_interval_s=interval_s)
    lines = [_run_one(candidate, context, settings) for candidate in result.candidates]

    print(f"Processing LinkedIn incoming: {incoming}")
    if lines:
        _print_jobs(lines)
    else:
        print("\nNothing to process.")
    _print_pending_and_ambiguous(result)

    print("\nSimulation mode: nothing was sent to LinkedIn.")
    if any(line.label.startswith(JobStatus.SIMULATED.value) for line in lines):
        print("Note: simulated posts stay in incoming/ and are simulated again on the next run")
        print("(duplicate detection arrives in Milestone 2).")
    print(f"Log: {settings.log_file}")
    return EXIT_JOB_PROBLEMS if any(line.problem for line in lines) else EXIT_OK


def _run_one(candidate: PostCandidate, context: WorkflowContext, settings: Settings) -> _JobLine:
    try:
        final = run_job(candidate, context)
    except Exception:  # A bug, not a publish outcome (the graph handles those). Keep going.
        logger.exception("Unexpected error; job abandoned: platform=%s stem=%s", candidate.platform, candidate.stem)
        return _JobLine(candidate.stem, "ERROR (unexpected)", "see the log for details", (), problem=True)
    return _describe(final, settings)


def _describe(final: JobState, settings: Settings) -> _JobLine:
    stem, status, errors = final["stem"], final["status"], tuple(final["errors"])
    destination = _relative_to_data(final["destination"], settings)

    if final["retry_later"]:
        return _JobLine(stem, "WAITING (still copying)", "will retry on next run", (), problem=False)
    if status == JobStatus.SIMULATED:
        return _JobLine(stem, "SIMULATED (not published)", "files stay in incoming/", errors, problem=False)
    if status == JobStatus.PUBLISHED:
        note = f"archived to {destination}" if destination else "files left in incoming/"
        return _JobLine(stem, status, note, errors, problem=bool(errors))
    note = f"moved to {destination}" if destination else "files left in incoming/"
    return _JobLine(stem, status, note, errors, problem=True)


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
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
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
