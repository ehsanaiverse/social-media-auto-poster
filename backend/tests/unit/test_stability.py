"""Tests for the file-copy stability check. No test really sleeps."""

import os
from pathlib import Path

import pytest

from social_media_auto_poster.ingestion.stability import is_file_stable


def no_sleep(_seconds: float) -> None:
    pass


def make_file(tmp_path: Path, content: bytes = b"image bytes") -> Path:
    path = tmp_path / "launch.jpg"
    path.write_bytes(content)
    return path


def test_unchanged_file_is_stable(tmp_path):
    path = make_file(tmp_path)

    assert is_file_stable(path, 1.0, sleep=no_sleep) is True


def test_sleep_receives_the_interval(tmp_path):
    path = make_file(tmp_path)
    waits = []

    is_file_stable(path, 2.5, sleep=waits.append)

    assert waits == [2.5]


def test_size_change_during_wait_is_not_stable(tmp_path):
    path = make_file(tmp_path)

    def grow(_seconds):
        with path.open("ab") as f:
            f.write(b" more bytes")

    assert is_file_stable(path, 1.0, sleep=grow) is False


def test_mtime_change_with_same_size_is_not_stable(tmp_path):
    path = make_file(tmp_path)
    original = path.stat().st_mtime_ns

    def bump_mtime(_seconds):
        # One NTFS tick (100 ns) later: finer than a float st_mtime can reliably
        # resolve at current timestamps, so only st_mtime_ns is guaranteed to notice.
        os.utime(path, ns=(original, original + 100))

    assert is_file_stable(path, 1.0, sleep=bump_mtime) is False


def test_file_deleted_during_wait_is_not_stable(tmp_path):
    path = make_file(tmp_path)

    assert is_file_stable(path, 1.0, sleep=lambda _s: path.unlink()) is False


def test_missing_file_is_not_stable(tmp_path):
    assert is_file_stable(tmp_path / "missing.jpg", 1.0, sleep=no_sleep) is False


def test_file_that_cannot_be_opened_is_not_stable(tmp_path, monkeypatch):
    path = make_file(tmp_path)

    def locked(*_args, **_kwargs):
        raise PermissionError("The process cannot access the file because it is being used.")

    monkeypatch.setattr(Path, "open", locked)

    assert is_file_stable(path, 1.0, sleep=no_sleep) is False


def test_empty_file_can_be_stable(tmp_path):
    path = make_file(tmp_path, b"")

    assert is_file_stable(path, 1.0, sleep=no_sleep) is True


def test_negative_interval_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="negative"):
        is_file_stable(make_file(tmp_path), -1.0, sleep=no_sleep)
