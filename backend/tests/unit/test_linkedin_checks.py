"""Checks made before anything is sent to LinkedIn: caption escaping and length, image size.

The images in tests/fixtures/images were written by Windows' own encoder (GDI+):
- baseline-7x5.jpg: JFIF, tables, then the size marker (SOF0).
- exif-9x4.jpg: JFIF, then an EXIF block (APP1), then tables and the size marker.
- image-6x3.png: a regular PNG.
GDI+ cannot write progressive JPEGs, so the progressive case is the real baseline
file with its size marker switched from SOF0 to SOF2, which has the same layout.
"""

import struct
from pathlib import Path

import pytest

from social_media_auto_poster.platforms.linkedin.caption import (
    MAX_CAPTION_LENGTH,
    caption_length,
    caption_problem,
    escape_caption,
)
from social_media_auto_poster.platforms.linkedin.image_size import (
    MAX_PIXELS,
    ImageSizeError,
    image_problem,
    image_size,
)

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"


def fixture(name: str) -> bytes:
    return (IMAGES / name).read_bytes()


def sof_offset(jpeg: bytes) -> int:
    """Offset of the 0xFF of the first size (SOF) marker, found the slow, obvious way."""
    return next(i for i in range(2, len(jpeg) - 1) if jpeg[i] == 0xFF and jpeg[i + 1] in (0xC0, 0xC2))


def with_size(data: bytes, width: int, height: int) -> bytes:
    """A real header with only its width and height fields changed."""
    if data.startswith(b"\x89PNG"):
        return data[:16] + struct.pack(">II", width, height) + data[24:]
    at = sof_offset(data) + 5  # 0xFF, marker, length (2), precision (1).
    return data[:at] + struct.pack(">HH", height, width) + data[at + 4:]


# Caption escaping


@pytest.mark.parametrize(("text", "expected"), [
    ("Plain words, commas; and 50% off!", "Plain words, commas; and 50% off!"),
    ("Launch (beta)", "Launch \\(beta\\)"),
    ("a|b{c}d[e]f<g>h*i_j~k\\l", "a\\|b\\{c\\}d\\[e\\]f\\<g\\>h\\*i\\_j\\~k\\\\l"),
    ("Thanks @Jane", "Thanks \\@Jane"),  # Mentions are not supported: plain text.
    ("#launch day #2026", "#launch day #2026"),  # Hashtags stay hashtags.
    ("Grüße #Ünïcode", "Grüße #Ünïcode"),
    ("C# and F# are languages", "C\\# and F\\# are languages"),  # Not hashtags: right after a letter.
    ("# heading and #", "\\# heading and \\#"),  # Not followed by a letter or digit.
    ("##double", "\\##double"),
    ("(#tag)", "\\(#tag\\)"),
    ("#my_tag", "#my\\_tag"),  # The hashtag ends before the underscore, which is escaped.
    ("Line one\nLine two", "Line one\nLine two"),
])
def test_escape_caption(text, expected):
    assert escape_caption(text) == expected


def test_every_reserved_character_is_escaped_when_not_a_hashtag():
    for char in "|{}@[]()<>#\\*_~":
        assert escape_caption(f" {char} ") == f" \\{char} "


# Caption length


def test_caption_at_the_limit_is_accepted_and_one_over_is_refused():
    assert caption_problem("x" * MAX_CAPTION_LENGTH) is None
    assert caption_problem("x" * (MAX_CAPTION_LENGTH + 1)) == "caption is 3001 characters; LinkedIn allows at most 3000"


def test_length_counts_the_text_as_written_not_the_escapes():
    text = "(" * MAX_CAPTION_LENGTH
    assert len(escape_caption(text)) == 2 * MAX_CAPTION_LENGTH
    assert caption_problem(text) is None


def test_emoji_count_as_two_which_errs_on_the_safe_side():
    assert caption_length("ok 🚀") == 5
    assert caption_problem("🚀" * 1500) is None
    assert caption_problem("🚀" * 1500 + "x") is not None


# Image size from real headers


@pytest.mark.parametrize(("name", "size"), [
    ("baseline-7x5.jpg", (7, 5)),
    ("exif-9x4.jpg", (9, 4)),
    ("image-6x3.png", (6, 3)),
])
def test_real_files(name, size):
    assert image_size(fixture(name)) == size
    assert image_problem(fixture(name)) is None


def test_jpeg_size_marker_after_exif_is_found():
    data = fixture("exif-9x4.jpg")
    exif = data.index(b"\xff\xe1")
    assert data[exif + 4:exif + 8] == b"Exif" and exif < sof_offset(data)  # The fixture really is laid out so.
    assert image_size(data) == (9, 4)


def test_progressive_jpeg():
    data = bytearray(fixture("baseline-7x5.jpg"))
    data[sof_offset(bytes(data)) + 1] = 0xC2  # SOF0 -> SOF2 (progressive): same layout.
    assert image_size(bytes(data)) == (7, 5)


def test_jpeg_with_padding_bytes_before_a_marker():
    data = fixture("baseline-7x5.jpg")
    padded = data[:2] + b"\xff\xff" + data[2:]  # Extra 0xFF fill bytes are allowed before any marker.
    assert image_size(padded) == (7, 5)


@pytest.mark.parametrize("name", ["baseline-7x5.jpg", "exif-9x4.jpg", "image-6x3.png"])
def test_too_many_pixels_is_refused(name):
    data = fixture(name)
    assert image_problem(with_size(data, 6013, 6012)) is None  # 36,150,156: just under.
    problem = image_problem(with_size(data, 6013, 6013))  # 36,156,169: over.
    assert problem == "image is 6013 x 6013 = 36,156,169 pixels; LinkedIn accepts less than 36,152,320"
    assert 6013 * 6012 < MAX_PIXELS < 6013 * 6013


@pytest.mark.parametrize("cut", [0, 1, 2, 3, 5, 8])
def test_truncated_jpeg_headers_are_refused(cut):
    data = fixture("exif-9x4.jpg")
    # The size segment is FF C0, length (2), precision (1), height (2), width (2): 9 bytes.
    truncated = data[: sof_offset(data) + cut]  # Cut just before or inside it.
    with pytest.raises(ImageSizeError, match="truncated"):
        image_size(truncated)
    assert image_problem(truncated).startswith("image size could not be read")


def test_a_header_cut_right_after_the_size_is_enough():
    data = fixture("exif-9x4.jpg")
    assert image_size(data[: sof_offset(data) + 9]) == (9, 4)


def test_truncated_inside_the_exif_block_is_refused():
    data = fixture("exif-9x4.jpg")
    with pytest.raises(ImageSizeError, match="truncated"):
        image_size(data[: data.index(b"Exif") + 10])


def test_truncated_png_header_is_refused():
    with pytest.raises(ImageSizeError, match="PNG header is truncated"):
        image_size(fixture("image-6x3.png")[:20])


def test_jpeg_without_a_size_before_the_image_data_is_refused():
    data = fixture("baseline-7x5.jpg")
    at = sof_offset(data)
    (length,) = struct.unpack(">H", data[at + 2:at + 4])
    without_sof = data[:at] + data[at + 2 + length:]  # Drop the whole size segment.
    with pytest.raises(ImageSizeError, match="image data starts before any size"):
        image_size(without_sof)


def test_garbage_and_unknown_formats_are_refused():
    for data in (b"", b"GIF89a....", b"\xff\xd8\x00\x00", b"\xff\xd8\xff\xe0\x00\x01"):
        assert image_problem(data).startswith("image size could not be read")


def test_zero_size_is_refused():
    assert image_problem(with_size(fixture("image-6x3.png"), 0, 3)) == "image size is 0 x 3 pixels"
