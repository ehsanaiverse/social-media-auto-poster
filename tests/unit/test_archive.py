"""Tests for moving a post's two files together. Temporary folders only."""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from social_media_auto_poster.models import Platform, PostCandidate
from social_media_auto_poster.services import archive
from social_media_auto_poster.services.archive import ArchiveError, archive_post

NOW = datetime(2026, 10, 25, 14, 30, 12, tzinfo=UTC)
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

    folder = archive_post(post, dest, now=NOW)

    assert folder == dest / "20261025T143012Z_launch"
    assert (folder / "launch.jpg").read_bytes() == b"image"
    assert (folder / "launch.txt").read_bytes() == b"caption"
    assert not post.media_path.exists()
    assert not post.caption_path.exists()


def test_dest_dir_is_created_if_missing(tmp_path):
    post = make_post(tmp_path)

    folder = archive_post(post, tmp_path / "deep" / "archive", now=NOW)

    assert folder.parent == tmp_path / "deep" / "archive"


def test_same_stem_twice_never_overwrites(tmp_path):
    dest = tmp_path / "archive"
    first = archive_post(make_post(tmp_path), dest, now=NOW)
    second = archive_post(make_post(tmp_path), dest, now=NOW)

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
        archive_post(post, dest, now=NOW)

    remaining = post.caption_path if missing == "media_path" else post.media_path
    assert remaining.exists()
    assert not dest.exists()


def test_different_drive_is_refused_before_anything_moves(tmp_path, monkeypatch):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    monkeypatch.setattr(archive, "_device", lambda path: 2 if path == dest else 1)

    with pytest.raises(ArchiveError, match="different drives"):
        archive_post(post, dest, now=NOW)

    assert post.media_path.exists()
    assert post.caption_path.exists()
    assert list(dest.iterdir()) == []


def test_failed_media_move_leaves_everything_in_place(tmp_path, monkeypatch):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    monkeypatch.setattr(os, "replace", fail_when_moving(post.media_path))

    with pytest.raises(PermissionError):
        archive_post(post, dest, now=NOW)

    assert post.media_path.exists()
    assert post.caption_path.exists()
    assert list(dest.iterdir()) == []


def test_failed_caption_move_puts_the_image_back(tmp_path, monkeypatch):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    monkeypatch.setattr(os, "replace", fail_when_moving(post.caption_path))

    with pytest.raises(PermissionError):
        archive_post(post, dest, now=NOW)

    assert post.media_path.read_bytes() == b"image"
    assert post.caption_path.exists()
    assert list(dest.iterdir()) == []


def test_failed_rollback_raises_with_both_locations_and_logs_critical(tmp_path, monkeypatch, caplog):
    post = make_post(tmp_path)
    dest = tmp_path / "archive"
    media_target = dest / "20261025T143012Z_launch" / "launch.jpg"
    monkeypatch.setattr(os, "replace", fail_when_moving(post.caption_path, also_fail_from=media_target))

    with caplog.at_level(logging.CRITICAL), pytest.raises(ArchiveError) as exc:
        archive_post(post, dest, now=NOW)

    message = str(exc.value)
    assert str(media_target) in message
    assert str(post.caption_path) in message
    assert "manual repair" in message
    assert media_target.exists()
    assert post.caption_path.exists()
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert "stem=launch" in critical[0].getMessage()
