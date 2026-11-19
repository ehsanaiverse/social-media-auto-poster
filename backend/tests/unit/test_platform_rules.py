"""The platform rules registry (platforms/rules.py) and the shared image header reader (platforms/image_header.py)."""

from pathlib import Path

from social_media_auto_poster.models import Platform, PostCandidate
from social_media_auto_poster.platforms import image_header
from social_media_auto_poster.platforms.linkedin import image_size as linkedin_image_size
from social_media_auto_poster.platforms.linkedin.caption import MAX_CAPTION_LENGTH, caption_problem
from social_media_auto_poster.platforms.linkedin.image_size import MAX_PIXELS, image_problem
from social_media_auto_poster.platforms.rules import RULES

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"


def _png(width: int, height: int) -> bytes:
    return PNG_HEADER + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def test_only_linkedin_has_rules():
    assert set(RULES) == {Platform.LINKEDIN, Platform.INSTAGRAM}
    assert RULES[Platform.LINKEDIN].platform is Platform.LINKEDIN
    assert RULES[Platform.LINKEDIN].display_name == "LinkedIn"


def test_linkedin_caption_rule_is_linkedins_own_check():
    rules = RULES[Platform.LINKEDIN]

    assert rules.caption_problems("x" * MAX_CAPTION_LENGTH) == []
    too_long = "x" * (MAX_CAPTION_LENGTH + 1)
    assert rules.caption_problems(too_long) == [caption_problem(too_long)]


def test_linkedin_real_media_rule_is_its_pixel_limit():
    rules = RULES[Platform.LINKEDIN]
    side = int(MAX_PIXELS**0.5) + 1  # side * side >= MAX_PIXELS.

    assert rules.real_media_problems(_png(1200, 627)) == []
    assert rules.real_media_problems(_png(side, side)) == [image_problem(_png(side, side))]
    assert rules.real_media_problems(b"not an image") == [image_problem(b"not an image")]


def test_linkedin_image_size_module_still_offers_the_moved_reader():
    # tests/unit/test_linkedin_checks.py imports these from the LinkedIn module.
    assert linkedin_image_size.image_size is image_header.image_size
    assert linkedin_image_size.ImageSizeError is image_header.ImageSizeError
    assert image_header.image_size(_png(320, 200)) == (320, 200)


def test_a_platform_without_rules_gets_only_the_shared_checks(tmp_path: Path):
    from social_media_auto_poster.ingestion.validation import validate_candidate

    media = tmp_path / "post.png"
    media.write_bytes(_png(10, 10))
    caption = tmp_path / "post.txt"
    caption.write_text("x" * (MAX_CAPTION_LENGTH + 1), encoding="utf-8")

    for platform, expected_valid in ((Platform.FACEBOOK, True), (Platform.LINKEDIN, False)):
        result = validate_candidate(PostCandidate(platform, "post", media, caption))
        assert result.is_valid is expected_valid
