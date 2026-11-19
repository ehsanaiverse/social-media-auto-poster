"""JPEG details read from the header: size, colour components, EXIF orientation, MPO.

The images in tests/fixtures/images were written by Windows' own encoders:
- baseline-7x5.jpg, exif-9x4.jpg: GDI+ (see test_linkedin_checks.py). exif-9x4.jpg's
  EXIF block (before the size marker) holds only an image description, no orientation.
- orientation6-portrait-4x5.jpg, orientation6-landscape-10x5.jpg: WPF's JpegBitmapEncoder
  with EXIF orientation 6 ("turn 90 degrees clockwise to show"), written big-endian ("MM").
  The sizes are as stored: 4x5 and 10x5.
- cmyk-6x4.jpg: WPF, from a Cmyk32 bitmap: an Adobe (APP14) segment and 4 components.
- grey-5x3.jpg: WPF, from a Gray8 bitmap: 1 component.

Windows cannot write every case, so some are made from these real files by the smallest change:
- progressive: the baseline file with its size marker switched from SOF0 to SOF2 (same layout);
- MPO: Windows has no MPO encoder, so an APP2 "MPF" segment is inserted right after SOI of a
  real file (real MPO files carry it before the first image's size marker, as here);
- little-endian EXIF and malformed EXIF: bytes changed inside the real EXIF block;
- truncated headers: real files cut short.
"""

import struct
from pathlib import Path

import pytest

from social_media_auto_poster.platforms.image_header import (
    ROTATED_ORIENTATIONS,
    ImageSizeError,
    JpegInfo,
    image_size,
    jpeg_info,
)

IMAGES = Path(__file__).resolve().parents[1] / "fixtures" / "images"
JPEG_FIXTURES = [
    "baseline-7x5.jpg",
    "exif-9x4.jpg",
    "orientation6-portrait-4x5.jpg",
    "orientation6-landscape-10x5.jpg",
    "cmyk-6x4.jpg",
    "grey-5x3.jpg",
]


def fixture(name: str) -> bytes:
    return (IMAGES / name).read_bytes()


def sof_offset(jpeg: bytes) -> int:
    """Offset of the 0xFF of the first size (SOF) marker, found the slow, obvious way."""
    return next(i for i in range(2, len(jpeg) - 1) if jpeg[i] == 0xFF and jpeg[i + 1] in (0xC0, 0xC2))


def tiff_offset(jpeg: bytes) -> int:
    """Offset of the TIFF header inside the EXIF block (right after "Exif\\0\\0")."""
    return jpeg.index(b"Exif\x00\x00") + 6


def patched(data: bytes, at: int, new: bytes) -> bytes:
    return data[:at] + new + data[at + len(new):]


def segment(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


# Real files


@pytest.mark.parametrize(("name", "expected"), [
    ("baseline-7x5.jpg", JpegInfo(7, 5, 3, None, False)),
    ("exif-9x4.jpg", JpegInfo(9, 4, 3, None, False)),  # EXIF without an orientation tag.
    ("orientation6-portrait-4x5.jpg", JpegInfo(4, 5, 3, 6, False)),
    ("orientation6-landscape-10x5.jpg", JpegInfo(10, 5, 3, 6, False)),
    ("cmyk-6x4.jpg", JpegInfo(6, 4, 4, None, False)),
    ("grey-5x3.jpg", JpegInfo(5, 3, 1, None, False)),
])
def test_real_files(name, expected):
    assert jpeg_info(fixture(name)) == expected


@pytest.mark.parametrize("name", JPEG_FIXTURES)
def test_image_size_agrees_with_jpeg_info(name):
    info = jpeg_info(fixture(name))
    assert image_size(fixture(name)) == (info.width, info.height)


def test_the_exif_fixtures_really_have_exif_before_the_size():
    for name in ("exif-9x4.jpg", "orientation6-portrait-4x5.jpg", "orientation6-landscape-10x5.jpg"):
        data = fixture(name)
        assert tiff_offset(data) < sof_offset(data)


def test_rotated_orientations():
    assert ROTATED_ORIENTATIONS == {5, 6, 7, 8}
    assert jpeg_info(fixture("orientation6-portrait-4x5.jpg")).orientation in ROTATED_ORIENTATIONS


def test_progressive_jpeg_reads_like_the_baseline():
    data = bytearray(fixture("baseline-7x5.jpg"))
    data[sof_offset(bytes(data)) + 1] = 0xC2  # SOF0 -> SOF2 (progressive): same layout.
    assert jpeg_info(bytes(data)) == jpeg_info(fixture("baseline-7x5.jpg"))


def test_padding_bytes_before_a_marker():
    data = fixture("orientation6-portrait-4x5.jpg")
    assert jpeg_info(data[:2] + b"\xff\xff" + data[2:]) == JpegInfo(4, 5, 3, 6, False)


# Multi-picture (MPO)


def test_an_mpf_segment_marks_a_multi_picture_file():
    data = fixture("orientation6-landscape-10x5.jpg")
    mpf = segment(0xE2, b"MPF\x00" + b"MM\x00\x2a\x00\x00\x00\x08")  # MPF id, then a minimal TIFF header.
    assert jpeg_info(data[:2] + mpf + data[2:]) == JpegInfo(10, 5, 3, 6, True)


def test_an_icc_profile_in_app2_is_not_multi_picture():
    data = fixture("baseline-7x5.jpg")
    icc = segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + b"\x00" * 16)
    assert jpeg_info(data[:2] + icc + data[2:]).multi_picture is False


def test_an_mpf_segment_after_the_size_is_not_seen():
    data = fixture("baseline-7x5.jpg")  # The walk stops at the first SOF, as documented.
    at = sof_offset(data)
    (length,) = struct.unpack(">H", data[at + 2:at + 4])
    after = at + 2 + length
    mpf = segment(0xE2, b"MPF\x00" + b"MM\x00\x2a\x00\x00\x00\x08")
    assert jpeg_info(data[:after] + mpf + data[after:]).multi_picture is False


# EXIF details

# The orientation fixtures' TIFF block (26 bytes, big-endian):
#   0  "MM"  2  42  4  IFD0 offset 8 | 8  entry count 1 |
#   10 tag 0x0112  12 type 3 (SHORT)  14 count 1  18 value 6, 2 bytes of padding | 22 next IFD 0
LITTLE_ENDIAN_TIFF = (
    b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", 1)
    + struct.pack("<HHIH", 0x0112, 3, 1, 6) + b"\x00\x00" + struct.pack("<I", 0)
)


def test_the_fixture_exif_block_is_laid_out_as_described():
    data = fixture("orientation6-portrait-4x5.jpg")
    at = tiff_offset(data)
    assert data[at:at + 22] == b"MM\x00\x2a\x00\x00\x00\x08\x00\x01\x01\x12\x00\x03\x00\x00\x00\x01\x00\x06\x00\x00"


def test_little_endian_exif():
    data = fixture("orientation6-portrait-4x5.jpg")
    assert jpeg_info(patched(data, tiff_offset(data), LITTLE_ENDIAN_TIFF)).orientation == 6


@pytest.mark.parametrize("value", [1, 2, 5, 7, 8])
def test_every_valid_orientation_value_is_read(value):
    data = fixture("orientation6-portrait-4x5.jpg")
    assert jpeg_info(patched(data, tiff_offset(data) + 18, struct.pack(">H", value))).orientation == value


@pytest.mark.parametrize(("offset", "new"), [
    pytest.param(0, b"XX", id="unknown byte order"),
    pytest.param(2, b"\x00\x2b", id="bad TIFF magic"),
    pytest.param(4, b"\x00\x00\x01\x00", id="IFD offset past the file"),
    pytest.param(4, b"\x00\x00\x00\x1a", id="IFD offset just past the segment"),  # 26: the next segment's bytes.
    pytest.param(4, b"\x00\x00\x00\x00", id="IFD offset inside the TIFF header"),
    pytest.param(8, b"\x00\x05", id="entry count past the segment"),  # Orientation entry is first: still read.
    pytest.param(12, b"\x00\x04", id="wrong type (LONG)"),
    pytest.param(14, b"\x00\x00\x00\x00", id="count 0"),
    pytest.param(18, b"\x00\x00", id="value 0"),
    pytest.param(18, b"\x00\x09", id="value 9"),
    pytest.param(18, b"\xff\xff", id="value 65535"),
])
def test_malformed_exif_gives_no_orientation_and_no_error(offset, new):
    data = fixture("orientation6-portrait-4x5.jpg")
    info = jpeg_info(patched(data, tiff_offset(data) + offset, new))
    expected = 6 if offset == 8 else None
    assert info == JpegInfo(4, 5, 3, expected, False)


def test_entries_past_the_segment_are_not_read():
    data = fixture("orientation6-portrait-4x5.jpg")
    at = tiff_offset(data)
    # Two entries claimed; the first is not orientation, the second would lie outside the segment.
    broken = patched(patched(data, at + 8, b"\x00\x02"), at + 10, b"\x01\x13")
    assert jpeg_info(broken) == JpegInfo(4, 5, 3, None, False)


def test_an_exif_block_too_short_for_a_tiff_header():
    data = fixture("baseline-7x5.jpg")
    exif = segment(0xE1, b"Exif\x00\x00MM\x00")
    assert jpeg_info(data[:2] + exif + data[2:]) == JpegInfo(7, 5, 3, None, False)


def test_only_the_first_exif_block_counts():
    data = fixture("orientation6-portrait-4x5.jpg")
    second = segment(0xE1, b"Exif\x00\x00" + LITTLE_ENDIAN_TIFF.replace(b"\x06\x00\x00\x00\x00\x00", b"\x03\x00\x00\x00\x00\x00"))
    at = sof_offset(data)
    assert jpeg_info(data[:at] + second + data[at:]).orientation == 6


def test_app1_that_is_not_exif_is_ignored():
    data = fixture("baseline-7x5.jpg")
    xmp = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>")
    assert jpeg_info(data[:2] + xmp + data[2:]) == JpegInfo(7, 5, 3, None, False)


# Broken headers


def test_not_a_jpeg():
    for data in (fixture("image-6x3.png"), b"", b"GIF89a....", b"\xff"):
        with pytest.raises(ImageSizeError, match="^not a JPEG file$"):
            jpeg_info(data)


@pytest.mark.parametrize("name", ["exif-9x4.jpg", "orientation6-landscape-10x5.jpg", "cmyk-6x4.jpg"])
@pytest.mark.parametrize("cut", [0, 1, 2, 3, 5, 8, 9])
def test_truncated_at_or_inside_the_size_segment(name, cut):
    data = fixture(name)
    # The size segment is FF C0, length (2), precision (1), height (2), width (2), Nf (1): 10 bytes.
    with pytest.raises(ImageSizeError, match="truncated"):
        jpeg_info(data[: sof_offset(data) + cut])


def test_cut_after_the_size_but_before_the_components():
    data = fixture("orientation6-portrait-4x5.jpg")
    cut = data[: sof_offset(data) + 9]
    assert image_size(cut) == (4, 5)  # Enough for the size, as before.
    with pytest.raises(ImageSizeError, match="^JPEG size segment is truncated$"):
        jpeg_info(cut)
    assert jpeg_info(data[: sof_offset(data) + 10]) == JpegInfo(4, 5, 3, 6, False)


def test_size_segment_with_length_7():
    data = fixture("baseline-7x5.jpg")
    short = patched(data, sof_offset(data) + 2, b"\x00\x07")  # Length covers the size but not Nf.
    assert image_size(short) == (7, 5)  # Unchanged: image_size accepts it.
    with pytest.raises(ImageSizeError, match="^JPEG size segment is truncated$"):
        jpeg_info(short)


@pytest.mark.parametrize("name", ["exif-9x4.jpg", "orientation6-portrait-4x5.jpg"])
@pytest.mark.parametrize("into", [0, 4, 8, 12, 20])
def test_truncated_inside_the_exif_block(name, into):
    data = fixture(name)
    with pytest.raises(ImageSizeError, match="^JPEG header is truncated before its size$"):
        jpeg_info(data[: tiff_offset(data) + into])


def test_truncated_before_the_size_segment():
    data = fixture("cmyk-6x4.jpg")
    for cut in (2, 3, 4, sof_offset(data) - 30):
        with pytest.raises(ImageSizeError, match="^JPEG header is truncated before its size$"):
            jpeg_info(data[:cut])


def _without_sof(data: bytes) -> bytes:
    at = sof_offset(data)
    (length,) = struct.unpack(">H", data[at + 2:at + 4])
    return data[:at] + data[at + 2 + length:]


@pytest.mark.parametrize("data", [
    pytest.param(b"\xff\xd8\x00\x00", id="no marker"),
    pytest.param(b"\xff\xd8\xff\xe0\x00\x01", id="invalid length"),
    pytest.param(b"\xff\xd8\xff\xe0", id="cut before a length"),
    pytest.param(_without_sof(fixture("baseline-7x5.jpg")), id="image data before any size"),
    pytest.param(fixture("exif-9x4.jpg")[:40], id="cut inside EXIF"),
])
def test_structural_errors_match_image_size(data):
    with pytest.raises(ImageSizeError) as from_size:
        image_size(data)
    with pytest.raises(ImageSizeError) as from_info:
        jpeg_info(data)
    assert str(from_info.value) == str(from_size.value)
