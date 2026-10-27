"""Move a post's media and caption together into a destination folder.

Used for ``archive/`` (only after a confirmed real publish; the graph enforces
that) and for ``failed/``. This module only moves files; it never decides
whether a post should be moved.

Moving is safe to repeat for the same job. A resumed graph step (Milestone 3)
may call ``archive_post`` again after a crash part-way through: the job's folder
is recognised by its marker file, files already there count as moved, and the
rest are moved now.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from social_media_auto_poster.models import JobStatus, PostCandidate

logger = logging.getLogger(__name__)

# Written into every post folder before any file moves; holds the job ID.
JOB_MARKER_NAME = ".autoposter-job"


class ArchiveError(Exception):
    """A post could not be moved safely. The message says where its files are."""


def post_folder_name(stem: str, when: datetime) -> str:
    """The folder name for a post: ``<UTC timestamp>_<stem>``."""
    return f"{when:%Y%m%dT%H%M%SZ}_{stem}"


def archive_post(candidate: PostCandidate, dest_dir: Path, *, job_id: str, folder_name: str) -> Path:
    """Move both files of ``candidate`` into ``dest_dir/folder_name`` and return that folder.

    First call for a job: the folder is created (``_2``, ``_3``... if a folder of
    that name belongs to another job, so an older post is never overwritten),
    marked with the job ID, and then both files move or neither does: if the
    caption move fails, the image is moved back.

    Repeated call for the same job (after a crash part-way through): the job's
    folder is found by its marker. Files already in it count as moved; files
    still in their source folder are moved now. If a file is in neither place,
    or cannot be moved, ``ArchiveError`` says where each file is.

    Moves use ``os.replace``, which is atomic on one drive and never copies, so a
    different drive is refused before anything moves.
    """
    existing = _find_job_folder(dest_dir, folder_name, job_id)
    if existing is not None:
        return _finish_move(candidate, existing)

    media, caption = candidate.media_path, candidate.caption_path
    for path in (media, caption):
        if not path.is_file():
            raise FileNotFoundError(f"Cannot move post '{candidate.stem}': {path} does not exist.")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for path in (media, caption):
        _require_same_drive(candidate, path, dest_dir)

    folder = _create_job_folder(dest_dir, folder_name, job_id)
    media_target = folder / media.name
    caption_target = folder / caption.name

    try:
        os.replace(media, media_target)
    except OSError:
        _remove_job_folder(folder)
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
            _log_split(candidate, media_target, caption)
            raise ArchiveError(message) from rollback_error
        _remove_job_folder(folder)
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
    """Write a plain-text explanation next to a post moved to ``failed/``. Overwrites an earlier one."""
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


def _finish_move(candidate: PostCandidate, folder: Path) -> Path:
    """Complete an earlier, interrupted move of this job into ``folder``."""
    for source in (candidate.media_path, candidate.caption_path):
        target = folder / source.name
        if target.is_file():
            continue  # Moved before the crash.
        if not source.is_file():
            raise ArchiveError(
                f"Post '{candidate.stem}' needs manual repair: {source.name} is neither in "
                f"{source.parent} nor in {folder}."
            )
        _require_same_drive(candidate, source, folder)
        try:
            os.replace(source, target)
        except OSError as error:
            _log_split(candidate, folder, source)
            raise ArchiveError(
                f"Post '{candidate.stem}' is split and needs manual repair: part of it is in {folder}, "
                f"{source.name} is still at {source} and could not be moved ({error})."
            ) from error
    logger.info("Finished an interrupted move: platform=%s stem=%s folder=%s", candidate.platform, candidate.stem, folder)
    return folder


def _find_job_folder(dest_dir: Path, folder_name: str, job_id: str) -> Path | None:
    """The folder an earlier attempt for ``job_id`` created, if any.

    An empty folder without a marker (a crash right after creating it) is claimed too.
    """
    for folder in _candidate_folders(dest_dir, folder_name):
        if not folder.is_dir():
            return None
        if _marker_job_id(folder) == job_id:
            return folder
        if not any(folder.iterdir()):
            (folder / JOB_MARKER_NAME).write_text(job_id, encoding="utf-8")
            return folder
    return None  # Unreachable: _candidate_folders never ends.


def _create_job_folder(dest_dir: Path, folder_name: str, job_id: str) -> Path:
    for folder in _candidate_folders(dest_dir, folder_name):
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        (folder / JOB_MARKER_NAME).write_text(job_id, encoding="utf-8")
        return folder
    raise AssertionError("unreachable")


def _candidate_folders(dest_dir: Path, folder_name: str):
    yield dest_dir / folder_name
    attempt = 2
    while True:
        yield dest_dir / f"{folder_name}_{attempt}"
        attempt += 1


def _marker_job_id(folder: Path) -> str | None:
    try:
        return (folder / JOB_MARKER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _remove_job_folder(folder: Path) -> None:
    (folder / JOB_MARKER_NAME).unlink(missing_ok=True)
    folder.rmdir()


def _require_same_drive(candidate: PostCandidate, path: Path, dest_dir: Path) -> None:
    if _device(path) != _device(dest_dir):
        raise ArchiveError(
            f"Cannot move post '{candidate.stem}': {path} and {dest_dir} are on different drives. "
            "Moves must stay on one drive to be atomic; nothing was moved or copied."
        )


def _log_split(candidate: PostCandidate, first: Path, second: Path) -> None:
    logger.critical(
        "Post files split during move: platform=%s stem=%s locations=%s, %s",
        candidate.platform,
        candidate.stem,
        first,
        second,
    )


def _device(path: Path) -> int:
    # Separate function so tests can simulate a second drive.
    return path.stat().st_dev
