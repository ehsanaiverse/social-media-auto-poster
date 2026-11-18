"""Command-line entry point: ``python -m social_media_auto_poster.main <command>``.

- ``scan``: a read-only report of what ingestion would pick up, including duplicates.
- ``jobs``: read-only job history from ``database/app.db``.
- ``run``: process every complete post once through the workflow graph,
  recording each job in ``database/app.db``. Publishing is simulated unless
  ``--real`` is given; real mode is never a default or a setting (D36).
  ``run --scheduled`` publishes only scheduled posts that are due (D46).
- ``resolve``: manually settle a NEEDS_REVIEW job as PUBLISHED or FAILED.
- ``linkedin-check``: check the LinkedIn token and setup; never creates a post.
- ``schedule`` / ``unschedule`` / ``reconfirm`` / ``schedules``: schedule a post for a date and time,
  cancel it, re-confirm it, or list schedules (D46). Every other run skips posts with an active schedule.

The work is done by ``operations/`` (shared with the local API in ``backend/app``); the command line
itself (parser, one handler per command, printing) is in ``cli/``. This file only starts it (D47).
"""

import sys

from social_media_auto_poster.cli.parser import _build_parser
from social_media_auto_poster.config import InvalidTimezoneError
from social_media_auto_poster.operations.common import EXIT_SETUP_ERROR

# Re-exported so existing callers can keep using ``main.<name>``. Nothing else should be added here:
# new code imports from operations/ or cli/ directly. Tests never patch these (test_patch_targets.py).
from social_media_auto_poster.config import load_settings  # noqa: F401  tests: test_data_dir_guard.py
from social_media_auto_poster.models import Platform  # noqa: F401  tests: test_linkedin_run.py, test_linkedin_setup.py
from social_media_auto_poster.operations.common import (  # noqa: F401
    EXIT_JOB_PROBLEMS,  # tests: test_end_to_end.py, test_run_logging.py
    EXIT_LOCKED,  # tests: test_end_to_end.py, test_resolve.py
    EXIT_OK,  # tests: test_end_to_end.py, test_history_commands.py, test_resolve.py
)
from social_media_auto_poster.operations.schedules import list_schedules, reconfirm_post  # noqa: F401  tests: test_reconfirm.py

# EXIT_SETUP_ERROR (imported above for main()) is also read by test_end_to_end.py, test_history_commands.py,
# test_resolve.py and test_run_logging.py; main() itself is called by the tests and by scripts/app.py.


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` (default: ``sys.argv[1:]``), run the command, and return its exit code."""
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except InvalidTimezoneError as error:  # A settings problem: say so plainly, never a traceback.
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_SETUP_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
