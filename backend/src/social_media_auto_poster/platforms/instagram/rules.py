"""Instagram's post rules, checked in every mode (D45, D49).

Captions: Instagram documents "Maximum 2200 characters, 30 hashtags, and 20 @
tags." (S13 in ``docs/instagram-api-notes.md``) and has errors for a caption that
is too long and for more than 20 @ tags (S20). It does not say how characters,
hashtags or mentions are counted, so the counting here is conservative (D49):

- length: the caption is refused if either its code points or its UTF-16 code
  units exceed 2,200 (an emoji is one code point but two UTF-16 units);
- hashtag: every ``#`` directly followed by a Unicode word character, anywhere,
  even inside a word ("C#1" counts, "C#" does not);
- mention: every ``@`` directly followed by ``[A-Za-z0-9._]``, anywhere, so an
  e-mail address counts as a mention.

Over-counting can refuse a caption Instagram would have accepted; that is the
safe failure. Under-counting would let a post fail at Instagram instead.

Pictures (S12, S13, S20): JPEG only, never converted, so the hashed bytes are the
published bytes (D33); at most 8 MB; an aspect ratio from 4:5 to 1.91:1. They are
read from the file header with the standard library (``platforms/image_header.py``,
D37). The cautious defaults agreed in D49: both aspect limits are inclusive, CMYK
pictures and multi-picture JPEGs (MPO) are refused, and a picture whose EXIF
orientation turns it by 90 degrees is refused when the stored and the turned
readings disagree about the aspect ratio (Instagram does not say which it uses).
"""

import re

from social_media_auto_poster.platforms.image_header import (
    JPEG_START,
    ROTATED_ORIENTATIONS,
    ImageSizeError,
    jpeg_info,
)

MAX_CAPTION_CHARS = 2200  # S13: "Maximum 2200 characters, 30 hashtags, and 20 @ tags."; S20: caption too long
MAX_HASHTAGS = 30  # S13
MAX_MENTIONS = 20  # S13; S20 error 2207040 (more than 20 @ tags)

# The lookahead matches each "#" or "@" once, so "##tag" and "@@user" count one each.
_HASHTAG = re.compile(r"#(?=\w)")
_MENTION = re.compile(r"@(?=[A-Za-z0-9._])")


def caption_lengths(text: str) -> tuple[int, int]:
    """(code points, UTF-16 code units) of the caption as written."""
    return len(text), len(text.encode("utf-16-le")) // 2


def count_hashtags(text: str) -> int:
    """Every ``#`` immediately followed by a Unicode word character."""
    return len(_HASHTAG.findall(text))


def count_mentions(text: str) -> int:
    """Every ``@`` immediately followed by a letter, digit, ``.`` or ``_`` (e-mail addresses count)."""
    return len(_MENTION.findall(text))


def caption_problems(text: str) -> list[str]:
    """Every reason this caption cannot be posted to Instagram: length, hashtags, mentions."""
    problems = []
    length = max(caption_lengths(text))
    if length > MAX_CAPTION_CHARS:
        problems.append(f"caption is {length:,} characters; Instagram allows at most 2,200")
    hashtags = count_hashtags(text)
    if hashtags > MAX_HASHTAGS:
        problems.append(f"caption has {hashtags} hashtags; Instagram allows at most 30")
    mentions = count_mentions(text)
    if mentions > MAX_MENTIONS:
        problems.append(
            f"caption has {mentions} @mentions (e-mail addresses count too); Instagram allows at most 20"
        )
    return problems


# S13: "File size: 8 MB maximum"; S20 (error 2207004): "less than 8 MiB". The smaller reading (decimal MB) is used.
MAX_IMAGE_BYTES = 8_000_000
# S13: "Aspect ratio: Must be within a 4:5 to 1.91:1 range"; S20 error 2207009. Inclusive (D49). As integer
# fractions, so the boundaries are exact: width / height >= 4 / 5 and width / height <= 191 / 100.
MIN_ASPECT = (4, 5)
MAX_ASPECT = (191, 100)

JPEG_ONLY = "Instagram accepts JPEG pictures only; export the picture as .jpg (pictures are never converted)"  # S12, S13
TOO_LARGE = "the picture is larger than 8,000,000 bytes (8 MB); Instagram accepts at most 8 MB"
MULTI_PICTURE = "the picture holds several images (an MPO file); Instagram does not accept it"  # S12
CMYK = "the picture uses CMYK colours; save it as an RGB JPEG"  # D49 (S13: "Color Space: sRGB")


def aspect_ok(width: int, height: int) -> bool:
    """Whether ``width`` x ``height`` is within 4:5 (tall) to 1.91:1 (wide), both limits included."""
    return width * MIN_ASPECT[1] >= height * MIN_ASPECT[0] and width * MAX_ASPECT[1] <= height * MAX_ASPECT[0]


def media_problems(data: bytes) -> list[str]:
    """Every reason this picture cannot be posted to Instagram. ``data`` is the file, read up to 8,000,001 bytes."""
    problems = [TOO_LARGE] if len(data) > MAX_IMAGE_BYTES else []
    if not data.startswith(JPEG_START):
        return [*problems, JPEG_ONLY]
    try:
        info = jpeg_info(data)
    except ImageSizeError as error:
        return [*problems, f"the picture's size could not be read ({error})"]
    if info.multi_picture:
        problems.append(MULTI_PICTURE)
    if info.components == 4:
        problems.append(CMYK)
    elif info.components not in (1, 3):  # Only grey (1) or colour (3) is accepted (D49, plan 6.1).
        problems.append(
            f"the picture has {info.components} colour components; Instagram accepts grey or RGB JPEGs only"
        )
    width, height = info.width, info.height
    if width == 0 or height == 0:
        return [*problems, f"the picture's size is {width} x {height}, which Instagram cannot use"]
    if info.orientation in ROTATED_ORIENTATIONS:
        stored, turned = aspect_ok(width, height), aspect_ok(height, width)
        if stored != turned:
            problems.append(
                f"the picture has rotation information (EXIF orientation {info.orientation}), so it is {width} x "
                f"{height} stored but {height} x {width} on screen, and Instagram accepts only one of the two; "
                "save it upright without rotation information"
            )
            return problems
        width, height = height, width  # Both readings agree; describe it as it is shown.
    if not aspect_ok(width, height):
        problems.append(
            f"the picture is {width} x {height}; Instagram accepts an aspect ratio from 4:5 (tall) to 1.91:1 (wide)"
        )
    return problems
