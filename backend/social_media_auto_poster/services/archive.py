"""Move a post's media and caption together into a destination folder.

Used for ``archive/`` (only after a confirmed real publish; the graph enforces
that) and for ``failed/``. This module only moves files; it never decides
whether a post should be moved.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from social_media_auto_poster.models import JobStatus, PostCandidate

logger = logging.getLogger(__name__)


class ArchiveError(Exception):
    """A post could not be moved safely. The message says where its files are."""


def archive_post(candidate: PostCandidate, dest_dir: Path, *, now: datetime | None = None) -> Path:
    """Move both files of ``candidate`` into a new folder under ``dest_dir`` and return it.

    The folder is ``<UTC timestamp>_<stem>`` (``_2``, ``_3``... on a clash), so
    an older post with the same stem is never overwritten.

    Both files move or neither does: if the caption move fails, the image is
    moved back. Moves use ``os.replace``, which is atomic on one drive and
    never copies, so a different drive is refused before anything moves.
    """
    media, caption = candidate.media_path, candidate.caption_path
    for path in (media, caption):
        if not path.is_file():
            raise FileNotFoundError(f"Cannot move post '{candidate.stem}': {path} does not exist.")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for path in (media, caption):
        if _device(path) != _device(dest_dir):
            raise ArchiveError(
                f"Cannot move post '{candidate.stem}': {path} and {dest_dir} are on different drives. "
                "Moves must stay on one drive to be atomic; nothing was moved or copied."
            )

    folder = _create_post_folder(dest_dir, candidate.stem, now or datetime.now(UTC))
    media_target = folder / media.name
    caption_target = folder / caption.name

    try:
        os.replace(media, media_target)
    except OSError:
        folder.rmdir()
        raise

    try:
        os.replace(caption, caption_target)
    except OSError as caption_error:
        try:
            os.replace(media_target, media)
        except OSError as rollback_error:
            message = (
                f"Post '{candidate.stem}' is split and needs manual repair: "
                f"image is at {media_target}, caption is still at {caption}. "
                f"Caption move failed ({caption_error}); moving the image back failed ({rollback_error})."
            )
            logger.critical(
                "Post files split during move: platform=%s stem=%s image=%s caption=%s",
                candidate.platform,
                candidate.stem,
                media_target,
                caption,
            )
            raise ArchiveError(message) from rollback_error
        folder.rmdir()
        raise

    return folder


ERROR_REPORT_NAME = "error-report.txt"


def write_error_report(
    folder: Path,
    *,
    job_id: str,
    stem: str,
    status: JobStatus,
    errors: list[str],
    now: datetime | None = None,
) -> Path:
    """Write a plain-text explanation next to a post moved to ``failed/``."""
    lines = [
        f"Post: {stem}",
        f"Job ID: {job_id}",
        f"Status: {status.value}",
        f"Time (UTC): {(now or datetime.now(UTC)):%Y-%m-%d %H:%M:%S}",
        "",
        "Reasons:",
        *(f"- {error}" for error in errors or ["(no details recorded)"]),
    ]
    if status is JobStatus.NEEDS_REVIEW:
        lines += [
            "",
            "The publish outcome is unknown: the post may or may not be live.",
            "Check LinkedIn before re-posting. Moving these files back to incoming/",
            "without checking may publish the post twice.",
        ]
    report = folder / ERROR_REPORT_NAME
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _create_post_folder(dest_dir: Path, stem: str, now: datetime) -> Path:
    base = f"{now:%Y%m%dT%H%M%SZ}_{stem}"
    attempt = 1
    while True:
        folder = dest_dir / (base if attempt == 1 else f"{base}_{attempt}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            attempt += 1


def _device(path: Path) -> int:
    # Separate function so tests can simulate a second drive.
    return path.stat().st_dev
