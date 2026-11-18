"""Tests for pairing media files with captions in an incoming folder."""

import logging
from pathlib import Path

import pytest

from social_media_auto_poster.ingestion.scanner import scan_incoming
from social_media_auto_poster.models import Platform

LINKEDIN = Platform.LINKEDIN


def touch(folder: Path, *names: str) -> None:
    for name in names:
        (folder / name).write_bytes(b"content")


def snapshot(folder: Path) -> dict[str, tuple[int, bytes]]:
    return {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in folder.iterdir() if p.is_file()}


def test_image_and_caption_with_same_stem_become_a_candidate(tmp_path):
    touch(tmp_path, "product-launch.jpg", "product-launch.txt")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.platform is LINKEDIN
    assert candidate.stem == "product-launch"
    assert candidate.media_path == tmp_path / "product-launch.jpg"
    assert candidate.caption_path == tmp_path / "product-launch.txt"
    assert result.pending == ()
    assert result.ambiguous == ()


def test_multiple_pairs_are_returned_in_sorted_order(tmp_path):
    touch(tmp_path, "b.png", "b.txt", "a.jpeg", "a.txt", "C.jpg", "c.txt")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert [c.stem for c in result.candidates] == ["a", "b", "C"]


def test_lone_image_and_lone_caption_are_pending(tmp_path):
    touch(tmp_path, "only-image.jpg", "only-caption.txt")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert result.candidates == ()
    assert result.pending == (tmp_path / "only-caption.txt", tmp_path / "only-image.jpg")


def test_unsupported_extension_is_still_paired_so_validation_can_reject_it(tmp_path):
    touch(tmp_path, "animated.gif", "animated.txt")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert [c.media_path.name for c in result.candidates] == ["animated.gif"]


def test_matching_is_case_insensitive_and_stem_comes_from_media(tmp_path):
    touch(tmp_path, "Launch.JPG", "launch.TXT")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert len(result.candidates) == 1
    assert result.candidates[0].stem == "Launch"


def test_two_images_for_one_stem_are_ambiguous(tmp_path, caplog):
    touch(tmp_path, "launch.jpg", "launch.png", "launch.txt", "other.jpg", "other.txt")

    with caplog.at_level(logging.WARNING):
        result = scan_incoming(tmp_path, LINKEDIN)

    assert result.ambiguous == (
        (tmp_path / "launch.jpg", tmp_path / "launch.png", tmp_path / "launch.txt"),
    )
    assert [c.stem for c in result.candidates] == ["other"]
    assert result.pending == ()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "stem=launch" in warnings[0].getMessage()
    assert "launch.png" in warnings[0].getMessage()


def test_two_images_without_a_caption_are_already_ambiguous(tmp_path):
    touch(tmp_path, "launch.jpg", "launch.png")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert result.ambiguous == ((tmp_path / "launch.jpg", tmp_path / "launch.png"),)
    assert result.pending == ()


def test_ambiguous_groups_are_one_per_stem_and_sorted(tmp_path, caplog):
    touch(tmp_path, "b.jpg", "b.png", "a.jpg", "a.png", "a.txt")

    with caplog.at_level(logging.WARNING):
        result = scan_incoming(tmp_path, LINKEDIN)

    assert result.ambiguous == (
        (tmp_path / "a.jpg", tmp_path / "a.png", tmp_path / "a.txt"),
        (tmp_path / "b.jpg", tmp_path / "b.png"),
    )
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


@pytest.mark.parametrize(
    "name",
    [".gitkeep", "~$draft.txt", "Thumbs.db", "desktop.ini", "launch.tmp", "launch.part", "launch.crdownload"],
)
def test_system_lock_and_partial_files_are_ignored(tmp_path, name):
    touch(tmp_path, name, "launch.jpg", "launch.txt")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert [c.stem for c in result.candidates] == ["launch"]
    assert result.pending == ()
    assert result.ambiguous == ()


def test_subfolders_are_skipped(tmp_path):
    (tmp_path / "launch").mkdir()
    touch(tmp_path, "launch.txt")

    result = scan_incoming(tmp_path, LINKEDIN)

    assert result.pending == (tmp_path / "launch.txt",)


def test_empty_folder_gives_empty_result(tmp_path):
    result = scan_incoming(tmp_path, LINKEDIN)

    assert (result.candidates, result.pending, result.ambiguous) == ((), (), ())


def test_missing_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        scan_incoming(tmp_path / "missing", LINKEDIN)


def test_scanning_never_moves_or_changes_files(tmp_path):
    touch(tmp_path, "a.jpg", "a.txt", "lone.png", "x.jpg", "x.png")
    before = snapshot(tmp_path)

    scan_incoming(tmp_path, LINKEDIN)

    assert snapshot(tmp_path) == before
