"""Tests for moving a post's two files together. Temporary folders only."""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from social_media_auto_poster.models import Platform, PostCandidate
from social_media_auto_poster.services import archive
from social_media_auto_poster.services.archive import JOB_MARKER_NAME, ArchiveError, archive_post, post_folder_name

NOW = datetime(2026, 10, 25, 14, 30, 12, tzinfo=UTC)
FOLDER = post_folder_name("launch", NOW)  # 20261025T143012Z_launch
real_replace = os.replace


def make_post(tmp_path: Path, stem: str = "launch") -> PostCandidate:
    incoming = tmp_path / "incoming"
    incoming.mkdir(exist_ok=True)
    media = incoming / f"{stem}.jpg"
    caption = incoming / f"{stem}.txt"
    media.write_bytes(b"image")
    caption.write_bytes(b"caption")
    return PostCandidate(Platform.LINKEDIN, stem, media, caption)


def fail_when_moving(source: Path, *, also_fail_from: Path | None = None):
    """An os.replace stand-in that fails for ``source`` (and optionally ``also_fail_from``)."""

    def fake_replace(src, dst):
        if Path(src) in (source, also_fail_from):
            raise PermissionError(13, "Permission denied", str(src))
        real_replace(src, dst)

    return fake_replace


def test_both_files_move_into_a_timestamped_folder(tmp_path):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"

    folder = archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert folder == dest / "20261025T143012Z_launch"
    assert (folder / "launch.jpg").read_bytes() == b"image"
    assert (folder / "launch.txt").read_bytes() == b"caption"
    assert not post.media_path.exists()
    assert not post.caption_path.exists()


def test_dest_dir_is_created_if_missing(tmp_path):
    post = make_post(tmp_path)

    folder = archive_post(post, tmp_path / "deep" / "archive", job_id="job-1", folder_name=FOLDER)

    assert folder.parent == tmp_path / "deep" / "archive"


def test_same_folder_name_for_another_job_never_overwrites(tmp_path):
    dest = tmp_path / "archive"
    first = archive_post(make_post(tmp_path), dest, job_id="job-1", folder_name=FOLDER)
    second = archive_post(make_post(tmp_path), dest, job_id="job-2", folder_name=FOLDER)

    assert first.name == "20261025T143012Z_launch"
    assert second.name == "20261025T143012Z_launch_2"
    assert (first / "launch.jpg").exists()
    assert (second / "launch.jpg").exists()


@pytest.mark.parametrize("missing", ["media_path", "caption_path"])
def test_missing_source_file_moves_nothing(tmp_path, missing):
    post = make_post(tmp_path)
    getattr(post, missing).unlink()
    dest = tmp_path / "archive"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    remaining = post.caption_path if missing == "media_path" else post.media_path
    assert remaining.exists()
    assert not dest.exists()


def test_different_drive_is_refused_before_anything_moves(tmp_path, monkeypatch):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    monkeypatch.setattr(archive, "_device", lambda path: 2 if path == dest else 1)

    with pytest.raises(ArchiveError, match="different drives"):
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert post.media_path.exists()
    assert post.caption_path.exists()
    assert list(dest.iterdir()) == []


def test_failed_media_move_leaves_everything_in_place(tmp_path, monkeypatch):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    monkeypatch.setattr(os, "replace", fail_when_moving(post.media_path))

    with pytest.raises(PermissionError):
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert post.media_path.exists()
    assert post.caption_path.exists()
    assert list(dest.iterdir()) == []


def test_failed_caption_move_puts_the_image_back(tmp_path, monkeypatch):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    monkeypatch.setattr(os, "replace", fail_when_moving(post.caption_path))

    with pytest.raises(PermissionError):
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert post.media_path.read_bytes() == b"image"
    assert post.caption_path.exists()
    assert list(dest.iterdir()) == []


def test_failed_rollback_raises_with_both_locations_and_logs_critical(tmp_path, monkeypatch, caplog):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    media_target = dest / "20261025T143012Z_launch" / "launch.jpg"
    monkeypatch.setattr(os, "replace", fail_when_moving(post.caption_path, also_fail_from=media_target))

    with caplog.at_level(logging.CRITICAL), pytest.raises(ArchiveError) as exc:
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    message = str(exc.value)
    assert str(media_target) in message
    assert str(post.caption_path) in message
    assert "manual repair" in message
    assert media_target.exists()
    assert post.caption_path.exists()
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert "stem=launch" in critical[0].getMessage()


# Marker file and repeating a move for the same job (resume after a crash, Milestone 3)


def test_the_folder_is_marked_with_the_job_id(tmp_path):
    folder = archive_post(make_post(tmp_path), tmp_path / "archive", job_id="job-1", folder_name=FOLDER)
    assert (folder / JOB_MARKER_NAME).read_text(encoding="utf-8") == "job-1"


def test_repeating_a_finished_move_is_a_no_op(tmp_path):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    first = archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    again = archive_post(post, dest, job_id="job-1", folder_name=FOLDER)  # Crash after both moves, before the checkpoint.

    assert again == first
    assert sorted(p.name for p in first.iterdir()) == [JOB_MARKER_NAME, "launch.jpg", "launch.txt"]
    assert [p.name for p in dest.iterdir()] == [FOLDER]


def test_crash_between_image_and_caption_is_finished_on_repeat(tmp_path, monkeypatch):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"

    def die_on_caption(src, dst):
        if Path(src) == post.caption_path:
            raise SystemExit("process killed")  # Not an OSError: no rollback, like a real crash.
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", die_on_caption)
    with pytest.raises(SystemExit):
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)
    monkeypatch.undo()
    assert (dest / FOLDER / "launch.jpg").exists() and post.caption_path.exists()  # Split.

    folder = archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert folder == dest / FOLDER
    assert (folder / "launch.jpg").read_bytes() == b"image"
    assert (folder / "launch.txt").read_bytes() == b"caption"
    assert not post.media_path.exists() and not post.caption_path.exists()


def test_crash_right_after_creating_the_folder_is_finished_on_repeat(tmp_path):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    (dest / FOLDER).mkdir(parents=True)  # Created, then the process died before the marker was written.

    folder = archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert folder == dest / FOLDER
    assert (folder / JOB_MARKER_NAME).read_text(encoding="utf-8") == "job-1"
    assert (folder / "launch.jpg").exists() and (folder / "launch.txt").exists()


def test_split_with_a_file_in_neither_place_is_reported_for_manual_action(tmp_path):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    folder = archive_post(post, dest, job_id="job-1", folder_name=FOLDER)
    (folder / "launch.txt").unlink()  # The caption is now nowhere.

    with pytest.raises(ArchiveError, match="manual repair") as exc:
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert "launch.txt" in str(exc.value) and str(folder) in str(exc.value)
    assert (folder / "launch.jpg").exists()


def test_split_that_cannot_be_finished_names_both_locations(tmp_path, monkeypatch, caplog):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    folder = archive_post(post, dest, job_id="job-1", folder_name=FOLDER)
    real_replace(folder / "launch.txt", post.caption_path)  # Image in the folder, caption still in incoming.
    monkeypatch.setattr(os, "replace", fail_when_moving(post.caption_path))

    with caplog.at_level(logging.CRITICAL), pytest.raises(ArchiveError, match="split") as exc:
        archive_post(post, dest, job_id="job-1", folder_name=FOLDER)

    assert str(folder) in str(exc.value) and str(post.caption_path) in str(exc.value)
    assert (folder / "launch.jpg").exists() and post.caption_path.exists()  # Nothing moved back or lost.
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_another_jobs_folder_is_never_taken_over(tmp_path):
    dest = tmp_path / "archive"
    other = archive_post(make_post(tmp_path, "launch"), dest, job_id="job-1", folder_name=FOLDER)

    mine = archive_post(make_post(tmp_path, "launch"), dest, job_id="job-2", folder_name=FOLDER)
    again = archive_post(make_post(tmp_path, "launch"), dest, job_id="job-2", folder_name=FOLDER)

    assert mine == again == dest / f"{FOLDER}_2"
    assert (other / JOB_MARKER_NAME).read_text(encoding="utf-8") == "job-1"


def test_an_old_folder_without_a_marker_is_never_taken_over(tmp_path):
    dest = tmp_path / "archive"
    old = dest / FOLDER
    old.mkdir(parents=True)
    (old / "launch.jpg").write_bytes(b"older post")  # From before Milestone 3: no marker.

    folder = archive_post(make_post(tmp_path), dest, job_id="job-1", folder_name=FOLDER)

    assert folder == dest / f"{FOLDER}_2"
    assert (old / "launch.jpg").read_bytes() == b"older post"
