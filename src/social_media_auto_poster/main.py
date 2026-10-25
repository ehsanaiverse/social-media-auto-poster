"""Command-line entry point: ``python -m social_media_auto_poster.main <command>``.

Only ``scan`` exists so far: a read-only report of what ingestion would pick
up. ``run`` (validate, simulate, record) is added in Milestone 1, Slice 6.
"""

import argparse
import sys
from pathlib import Path

from social_media_auto_poster.config import DATA_DIR_VAR, load_settings
from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.ingestion.stability import is_file_stable
from social_media_auto_poster.models import Platform, PostCandidate, ScanResult

DEFAULT_INTERVAL_S = 1.0


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
        description="Report candidates, pending files, and ambiguous stems. Never moves, validates, or publishes.",
    )
    scan.add_argument(
        "--dir",
        type=Path,
        help="Folder to scan instead of the configured LinkedIn incoming folder.",
    )
    scan.add_argument(
        "--interval",
        type=_non_negative_float,
        default=DEFAULT_INTERVAL_S,
        help=f"Seconds to watch each file for changes (default: {DEFAULT_INTERVAL_S}; 0 checks only for locks).",
    )
    scan.set_defaults(handler=_scan)
    return parser


def _scan(args: argparse.Namespace) -> int:
    folder = args.dir if args.dir is not None else load_settings().incoming_dir(Platform.LINKEDIN)
    folder = folder.resolve()
    try:
        result = scan_incoming(folder, Platform.LINKEDIN)
    except FileNotFoundError:
        print(f"Error: incoming folder not found: {folder}", file=sys.stderr)
        print(f"Create it, or set {DATA_DIR_VAR}, or pass --dir.", file=sys.stderr)
        return 1

    print(f"Scanning LinkedIn incoming: {folder}")
    if not (result.candidates or result.pending or result.ambiguous):
        print("\nNothing found.")
    else:
        _print_report(result, args.interval)
    print("\nDry run: nothing was moved, validated, or published.")
    return 0


def _print_report(result: ScanResult, interval_s: float) -> None:
    if result.candidates:
        pairs = [f"{c.media_path.name} + {c.caption_path.name}" for c in result.candidates]
        stem_width = max(len(c.stem) for c in result.candidates)
        pair_width = max(len(p) for p in pairs)
        print(f"\nCANDIDATES ({len(result.candidates)})")
        for candidate, pair in zip(result.candidates, pairs):
            label = _stability_label(candidate, interval_s)
            print(f"  {candidate.stem:<{stem_width}}   {pair:<{pair_width}}   {label}")

    if result.pending:
        print(f"\nPENDING ({len(result.pending)}) - waiting for a partner file")
        for path in result.pending:
            print(f"  {path.name}")

    if result.ambiguous:
        print(f"\nAMBIGUOUS ({len(result.ambiguous)}) - fix by hand, more than one file per stem")
        for group in result.ambiguous:
            print(f"  {group[0].stem}: {', '.join(p.name for p in group)}")


def _stability_label(candidate: PostCandidate, interval_s: float) -> str:
    # Both files must be complete before a post can be processed.
    files = {"media": candidate.media_path, "caption": candidate.caption_path}
    unstable = [role for role, path in files.items() if not is_file_stable(path, interval_s)]
    if not unstable:
        return "stable"
    return f"NOT STABLE ({' and '.join(unstable)} changing or locked)"


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
