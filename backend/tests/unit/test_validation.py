"""Tests for candidate validation. Image headers are built in memory; no Pillow."""

from pathlib import Path

import pytest

from social_media_auto_poster.ingestion.validation import MAX_CAPTION_BYTES, validate_candidate
from social_media_auto_poster.models import Platform, PostCandidate

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_post(
    folder: Path,
    media_name: str = "launch.jpg",
    media: bytes | None = JPEG_BYTES,
    caption: bytes | None = b"Our product is live!",
) -> PostCandidate:
    media_path = folder / media_name
    caption_path = folder / (media_path.stem + ".txt")
    if media is not None:
        media_path.write_bytes(media)
    if caption is not None:
        caption_path.write_bytes(caption)
    return PostCandidate(Platform.LINKEDIN, media_path.stem, media_path, caption_path)


@pytest.mark.parametrize(
    ("name", "media"),
    [("launch.jpg", JPEG_BYTES), ("launch.jpeg", JPEG_BYTES), ("launch.JPG", JPEG_BYTES), ("launch.png", PNG_BYTES)],
    ids=["jpg", "jpeg", "upper-JPG", "png"],
)
def test_valid_post_passes(tmp_path, name, media):
    result = validate_candidate(make_post(tmp_path, name, media))

    assert result.is_valid is True
    assert result.errors == ()


def test_unsupported_extension_is_rejected(tmp_path):
    result = validate_candidate(make_post(tmp_path, "launch.gif", b"GIF89a"))

    assert result.errors == ("unsupported image type '.gif' (allowed: .jpg, .jpeg, .png)",)


def test_empty_image_is_rejected(tmp_path):
    result = validate_candidate(make_post(tmp_path, media=b""))

    assert result.errors == ("image file is empty",)


def test_text_renamed_to_jpg_is_rejected(tmp_path):
    result = validate_candidate(make_post(tmp_path, media=b"just some text"))

    assert result.errors == ("image content is not a valid JPEG",)


def test_png_named_jpg_is_rejected(tmp_path):
    result = validate_candidate(make_post(tmp_path, "launch.jpg", PNG_BYTES))

    assert result.errors == ("file is PNG but named .jpg",)


def test_jpeg_named_png_is_rejected(tmp_path):
    result = validate_candidate(make_post(tmp_path, "launch.png", JPEG_BYTES))

    assert result.errors == ("file is JPEG but named .png",)


def test_invalid_utf8_caption_is_rejected(tmp_path):
    result = validate_candidate(make_post(tmp_path, caption=b"caf\xe9"))  # Latin-1, not UTF-8

    assert result.errors == ("caption is not valid UTF-8 text",)


def test_caption_with_bom_is_accepted(tmp_path):
    result = validate_candidate(make_post(tmp_path, caption="﻿Hello".encode()))

    assert result.is_valid is True


def test_non_ascii_utf8_caption_is_accepted(tmp_path):
    result = validate_candidate(make_post(tmp_path, caption="Café ☕ نیا".encode()))

    assert result.is_valid is True


@pytest.mark.parametrize("caption", [b"", b"   \r\n\t ", "﻿  ".encode()], ids=["empty", "whitespace", "bom-only"])
def test_empty_or_whitespace_caption_is_rejected(tmp_path, caption):
    result = validate_candidate(make_post(tmp_path, caption=caption))

    assert result.errors == ("caption is empty",)


def test_caption_at_size_cap_is_accepted(tmp_path):
    result = validate_candidate(make_post(tmp_path, caption=b"a" * MAX_CAPTION_BYTES))

    assert result.is_valid is True


def test_caption_over_size_cap_is_rejected(tmp_path):
    result = validate_candidate(make_post(tmp_path, caption=b"a" * (MAX_CAPTION_BYTES + 1)))

    assert result.errors == ("caption file too large (over 64 KB)",)


def test_missing_media_reports_only_not_found_for_it(tmp_path):
    # A .gif name would also be "unsupported"; only "not found" must be reported.
    result = validate_candidate(make_post(tmp_path, "launch.gif", media=None))

    assert result.errors == ("media file not found",)


def test_missing_caption_reports_only_not_found_for_it(tmp_path):
    result = validate_candidate(make_post(tmp_path, caption=None))

    assert result.errors == ("caption file not found",)


def test_missing_file_does_not_hide_errors_in_the_other_file(tmp_path):
    result = validate_candidate(make_post(tmp_path, media=None, caption=b"  "))

    assert result.errors == ("media file not found", "caption is empty")


def test_all_errors_are_collected(tmp_path):
    result = validate_candidate(make_post(tmp_path, "launch.gif", b"GIF89a", caption=b""))

    assert result.is_valid is False
    assert len(result.errors) == 2


def test_unreadable_files_are_reported(tmp_path, monkeypatch):
    post = make_post(tmp_path)

    def locked(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", locked)

    result = validate_candidate(post)

    assert result.errors == (
        "media file could not be read: Permission denied",
        "caption file could not be read: Permission denied",
    )


def test_validation_never_changes_files(tmp_path):
    post = make_post(tmp_path)
    before = (post.media_path.read_bytes(), post.caption_path.read_bytes())

    validate_candidate(post)

    assert (post.media_path.read_bytes(), post.caption_path.read_bytes()) == before
