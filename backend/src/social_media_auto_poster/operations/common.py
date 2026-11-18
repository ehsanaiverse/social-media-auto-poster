"""Constants and small helpers shared by the operations, the CLI and the API."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from social_media_auto_poster.config import DATA_DIR_VAR, Settings
from social_media_auto_poster.models import Platform
from social_media_auto_poster.storage.jobs import JobRecord

# Every module of the operations and the CLI logs under this name, so log lines read exactly as they did
# when all of this lived in main.py ("... run_id=... social_media_auto_poster.main Run finished: ...").
LOGGER_NAME = "social_media_auto_poster.main"

PLATFORM = Platform.LINKEDIN  # The only platform in scope.
DEFAULT_INTERVAL_S = 1.0

EXIT_OK = 0
EXIT_SETUP_ERROR = 1  # Also: run --real refused before publishing anything.
EXIT_USAGE = 2  # argparse uses it too.
EXIT_JOB_PROBLEMS = 3
EXIT_LOCKED = 4

MIN_JOB_ID_PREFIX = 6
DUPLICATE_LABEL = "DUPLICATE"
REAL_CONFIRMATION = "publish to LinkedIn"  # The API requires this text to start a real run.


class RealPublishingUnavailable(Exception):
    """Real publishing cannot start; the message says why."""


def outside_warning(settings: Settings) -> str:
    return (
        f"Warning: the data folder is outside backend\\: {settings.data_dir} "
        f"(check {DATA_DIR_VAR} in backend\\.env)"
    )


def _local_time(moment: datetime, zone: ZoneInfo) -> str:
    return f"{moment.astimezone(zone):%Y-%m-%d %H:%M} {zone.key}"


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


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
