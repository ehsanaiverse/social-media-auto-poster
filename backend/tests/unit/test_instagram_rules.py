"""Instagram's post rules registered in platforms/rules.py and applied by validation, in every mode (step B, D49).

Images are the real files in tests/fixtures/images (written by Windows' encoders, see test_image_header_jpeg.py);
a few cases replace only the width and height in the SOF segment, which is all these header-only checks read.
Instagram has no adapter yet: these rules are reachable only through validation in tests, never by a run,
a scan, a schedule or the API (the refusal tests in test_platform_parameter.py and test_platform_field.py).
"""

import dataclasses
import json
import struct
from pathlib import Path

import pytest

from social_media_auto_poster.ingestion import validation
from social_media_auto_poster.ingestion.validation import validate_candidate
from social_media_auto_poster.models import Platform, PostCandidate
from social_media_auto_poster.operations import platforms as operations_platforms
from social_media_auto_poster.platforms import image_header
from social_media_auto_poster.platforms import rules as platform_rules
from social_media_auto_poster.platforms.instagram import rules as instagram
from social_media_auto_poster.platforms.rules import RULES, UnsupportedPlatform, rules_for

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CASES = json.loads((FIXTURES / "post_rules_cases.json").read_text(encoding="utf-8"))
PROBLEM_TEXT = {
    "aspect": "Instagram accepts an aspect ratio from 4:5 (tall) to 1.91:1 (wide)",
    "orientation": "the picture has rotation information (EXIF orientation",
    "cmyk": instagram.CMYK,
    "jpeg_only": instagram.JPEG_ONLY,
    "components": "colour components; Instagram accepts grey or RGB JPEGs only",
    "zero_size": "which Instagram cannot use",
    "unreadable": "the picture's size could not be read (",
}


def _caption(case: dict) -> str:
    repeat = case.get("repeat", {"text": "", "times": 0})
    return case.get("prefix", "") + repeat["text"] * repeat["times"] + case.get("suffix", "")


def _sof_position(data: bytes) -> int:
    for marker, position, _length in image_header._jpeg_segments(data):
        if marker in image_header._SOF_MARKERS:
            return position  # Where the SOF length field starts: length (2), precision (1), height, width, Nf.
    raise AssertionError("no SOF segment")


def _image(case: dict) -> bytes:
    data = bytearray((FIXTURES / "images" / case["fixture"]).read_bytes())
    if case.get("sof_size"):
        width, height = case["sof_size"]
        position = _sof_position(bytes(data))
        data[position + 3 : position + 7] = struct.pack(">HH", height, width)
    if case.get("sof_components") is not None:
        data[_sof_position(bytes(data)) + 7] = case["sof_components"]
    if case.get("truncate_at") is not None:
        data = data[: case["truncate_at"]]
    return bytes(data)


def _post(tmp_path: Path, platform: Platform, media: bytes, *, suffix=".jpg", caption="Launch day!") -> PostCandidate:
    media_path = tmp_path / f"post{suffix}"
    media_path.write_bytes(media)
    caption_path = tmp_path / "post.txt"
    caption_path.write_text(caption, encoding="utf-8")
    return PostCandidate(platform, "post", media_path, caption_path)


# The registry


def test_instagram_has_rules_but_still_no_adapter():
    assert RULES[Platform.INSTAGRAM].display_name == "Instagram"
    assert rules_for(Platform.INSTAGRAM) is RULES[Platform.INSTAGRAM]
    assert operations_platforms.adapter_for(Platform.INSTAGRAM).real_available is False


def test_rules_for_refuses_a_platform_without_rules():
    with pytest.raises(UnsupportedPlatform, match="Facebook has no post rules"):
        rules_for(Platform.FACEBOOK)
    assert operations_platforms.UnsupportedPlatform is platform_rules.UnsupportedPlatform


def test_an_every_mode_image_rule_needs_its_read_limit():
    linkedin = RULES[Platform.LINKEDIN]
    with pytest.raises(ValueError, match="given together"):
        dataclasses.replace(linkedin, media_problems=instagram.media_problems)
    with pytest.raises(ValueError, match="given together"):
        dataclasses.replace(linkedin, media_read_limit=10)
    with pytest.raises(ValueError, match="at least 1"):
        dataclasses.replace(linkedin, media_problems=instagram.media_problems, media_read_limit=0)


def test_linkedin_keeps_no_every_mode_image_rule():
    linkedin = RULES[Platform.LINKEDIN]
    assert linkedin.media_problems is None and linkedin.media_read_limit is None


# Shared cases (also for the dashboard from step F)


@pytest.mark.parametrize("case", CASES["captions"], ids=lambda case: case["name"])
def test_caption_cases(case):
    text = _caption(case)

    assert instagram.caption_lengths(text) == (case["code_points"], case["utf16_units"])
    assert instagram.count_hashtags(text) == case["hashtags"]
    assert instagram.count_mentions(text) == case["mentions"]
    assert (instagram.caption_problems(text) == []) is case["valid"]


@pytest.mark.parametrize("case", CASES["images"], ids=lambda case: case["name"])
def test_image_cases(case):
    problems = instagram.media_problems(_image(case))

    if case["valid"]:
        assert problems == []
    else:
        assert len(problems) == 1
        assert PROBLEM_TEXT[case["problem"]] in problems[0]


# Through validation, as scan, runs, uploads and schedules see it


def test_the_size_limit_is_8_000_000_bytes_exactly(tmp_path):
    jpeg = (FIXTURES / "images" / "baseline-7x5.jpg").read_bytes()
    at_limit = jpeg + b"\x00" * (instagram.MAX_IMAGE_BYTES - len(jpeg))
    assert len(at_limit) == 8_000_000

    assert validate_candidate(_post(tmp_path, Platform.INSTAGRAM, at_limit)).is_valid
    over = validate_candidate(_post(tmp_path, Platform.INSTAGRAM, at_limit + b"\x00"))
    assert over.errors == (instagram.TOO_LARGE,)


def test_a_png_is_refused_for_instagram_and_accepted_for_linkedin(tmp_path):
    png = (FIXTURES / "images" / "image-6x3.png").read_bytes()

    assert validate_candidate(_post(tmp_path, Platform.INSTAGRAM, png, suffix=".png")).errors == (instagram.JPEG_ONLY,)
    assert validate_candidate(_post(tmp_path, Platform.LINKEDIN, png, suffix=".png")).is_valid


def test_an_mpo_file_is_refused(tmp_path):
    jpeg = (FIXTURES / "images" / "baseline-7x5.jpg").read_bytes()
    mpf = b"MPF\x00" + b"MM\x00\x2a\x00\x00\x00\x08"
    mpo = jpeg[:2] + b"\xff\xe2" + struct.pack(">H", len(mpf) + 2) + mpf + jpeg[2:]

    assert validate_candidate(_post(tmp_path, Platform.INSTAGRAM, mpo)).errors == (instagram.MULTI_PICTURE,)


def test_the_shared_image_checks_still_come_first(tmp_path):
    # A PNG named .jpg is the shared "file is PNG but named .jpg" error, not Instagram's JPEG-only text.
    png = (FIXTURES / "images" / "image-6x3.png").read_bytes()
    assert validate_candidate(_post(tmp_path, Platform.INSTAGRAM, png)).errors == ("file is PNG but named .jpg",)


def test_caption_and_image_problems_are_all_reported(tmp_path):
    wide = (FIXTURES / "images" / "exif-9x4.jpg").read_bytes()
    result = validate_candidate(_post(tmp_path, Platform.INSTAGRAM, wide, caption="#t " * 31))

    assert len(result.errors) == 2
    assert PROBLEM_TEXT["aspect"] in result.errors[0]
    assert result.errors[1] == "caption has 31 hashtags; Instagram allows at most 30"


def test_linkedin_still_reads_only_the_first_bytes_of_the_image(tmp_path, monkeypatch):
    # LinkedIn has no every-mode image rule, so validation must not read the whole file for it.
    jpeg = (FIXTURES / "images" / "baseline-7x5.jpg").read_bytes()  # Before the watch starts.
    reads = []
    real_open = Path.open

    def watched_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.suffix == ".jpg":
            real_read = handle.read
            handle.read = lambda size=-1: reads.append(size) or real_read(size)
        return handle

    monkeypatch.setattr(Path, "open", watched_open)

    assert validate_candidate(_post(tmp_path, Platform.LINKEDIN, jpeg)).is_valid
    assert reads == [len(validation.PNG_SIGNATURE)]
