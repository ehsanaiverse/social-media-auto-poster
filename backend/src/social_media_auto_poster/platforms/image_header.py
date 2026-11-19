"""Read an image's width and height (and a few JPEG details) from its file header, without decoding it.

Platforms limit image sizes, and rather than adding an imaging library the size
is read from the header with the standard library (D37):

- PNG: the IHDR chunk, which must come first, right after the 8-byte signature.
- JPEG: the start-of-frame (SOF) segment. It is not at a fixed place: segments
  such as EXIF (APP1), JFIF (APP0), quantization and Huffman tables can come
  before it, so the segments are walked one by one until a SOF marker appears.

``jpeg_info`` walks the same JPEG segments and also reports what some platforms
care about: the number of colour components (CMYK has 4), the EXIF orientation
(a photo stored sideways and shown turned), and whether the file is a
multi-picture JPEG (MPO).

An image whose size cannot be read is refused rather than guessed.
"""

import struct
from collections.abc import Iterator
from dataclasses import dataclass

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_START = b"\xff\xd8"

# Start-of-frame markers carry the size: C0-CF, except C4 (Huffman tables), C8 (reserved), CC (arithmetic coding).
_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers with no length field: TEM, RST0-RST7. (SOI and EOI never appear between segments.)
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_START_OF_SCAN = 0xDA
_APP1 = 0xE1  # EXIF.
_APP2 = 0xE2  # ICC profiles, and the MPF index of a multi-picture (MPO) file.

_EXIF_HEADER = b"Exif\x00\x00"
_MPF_HEADER = b"MPF\x00"
_ORIENTATION_TAG = 0x0112
_TIFF_SHORT = 3

# EXIF orientations 5-8 are shown turned by 90 degrees, so width and height swap on screen.
ROTATED_ORIENTATIONS: frozenset[int] = frozenset({5, 6, 7, 8})


class ImageSizeError(ValueError):
    """The width and height could not be read from the image header."""


@dataclass(frozen=True)
class JpegInfo:
    """What the header of a JPEG says, read up to its first SOF segment."""

    width: int  # From the SOF segment, as stored (not rotated).
    height: int
    components: int  # SOF "Nf": 1 grey, 3 colour (YCbCr/RGB), 4 CMYK/YCCK.
    orientation: int | None  # EXIF orientation 1..8; None if absent or unreadable.
    multi_picture: bool  # An MPF segment (APP2) came before the SOF: an MPO file.


def image_size(data: bytes) -> tuple[int, int]:
    """``(width, height)`` of a PNG or JPEG image, read from its header."""
    if data.startswith(PNG_SIGNATURE):
        return _png_size(data)
    if data.startswith(JPEG_START):
        return _jpeg_size(data)
    raise ImageSizeError("not a PNG or JPEG file")


def jpeg_info(data: bytes) -> JpegInfo:
    """Size, colour components, EXIF orientation and MPO flag of a JPEG, read from its header."""
    if not data.startswith(JPEG_START):
        raise ImageSizeError("not a JPEG file")
    orientation = None
    exif_seen = False
    multi_picture = False
    for marker, position, length in _jpeg_segments(data):
        if marker in _SOF_MARKERS:
            # Length (2), precision (1), height (2), width (2), number of components (1).
            if length < 8 or position + 8 > len(data):
                raise ImageSizeError("JPEG size segment is truncated")
            height, width, components = struct.unpack(">HHB", data[position + 3 : position + 8])
            return JpegInfo(width, height, components, orientation, multi_picture)
        # The segment's own bytes, after its length field. A segment cut short by the end of
        # the file is shorter; the walk raises "truncated" on the next step anyway.
        segment = data[position + 2 : position + length]
        if marker == _APP1 and not exif_seen and segment.startswith(_EXIF_HEADER):
            exif_seen = True  # Only the first EXIF block counts, as for image viewers.
            orientation = _exif_orientation(segment[len(_EXIF_HEADER) :])
        elif marker == _APP2 and segment.startswith(_MPF_HEADER):
            multi_picture = True
    raise AssertionError("unreachable: _jpeg_segments stops at a SOF or raises")  # pragma: no cover


def _png_size(data: bytes) -> tuple[int, int]:
    # Signature (8), then the first chunk: length (4), type (4), width (4), height (4).
    if len(data) < 24:
        raise ImageSizeError("PNG header is truncated")
    if data[12:16] != b"IHDR":
        raise ImageSizeError("PNG does not start with an IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int]:
    for marker, position, length in _jpeg_segments(data):
        if marker in _SOF_MARKERS:
            # Length (2), precision (1), height (2), width (2).
            if length < 7 or position + 7 > len(data):
                raise ImageSizeError("JPEG size segment is truncated")
            height, width = struct.unpack(">HH", data[position + 3 : position + 7])
            return width, height
    raise AssertionError("unreachable: _jpeg_segments stops at a SOF or raises")  # pragma: no cover


def _jpeg_segments(data: bytes) -> Iterator[tuple[int, int, int]]:
    """Yield ``(marker, position, length)`` for each JPEG segment, up to and including the first SOF.

    ``position`` is where the segment's 2-byte length field starts, and ``length`` counts
    that field too. The walk stops after yielding a SOF segment; a header that ends, breaks
    the segment structure, or reaches the image data (SOS) first raises ``ImageSizeError``.
    """
    position = 2  # After SOI.
    while True:
        # Each segment starts with 0xFF; extra 0xFF bytes are allowed as padding.
        if position >= len(data):
            raise ImageSizeError("JPEG header is truncated before its size")
        if data[position] != 0xFF:
            raise ImageSizeError(f"JPEG segment marker expected at byte {position}")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise ImageSizeError("JPEG header is truncated before its size")
        marker = data[position]
        position += 1
        if marker in _STANDALONE:
            continue
        if marker == _START_OF_SCAN:
            raise ImageSizeError("JPEG image data starts before any size")
        if position + 2 > len(data):
            raise ImageSizeError("JPEG header is truncated before its size")
        (length,) = struct.unpack(">H", data[position : position + 2])
        if length < 2:
            raise ImageSizeError(f"JPEG segment at byte {position} has an invalid length")
        yield marker, position, length
        if marker in _SOF_MARKERS:
            return
        position += length


def _exif_orientation(tiff: bytes) -> int | None:
    """The orientation (1..8) in an EXIF block, or None if it has none or cannot be read.

    After "Exif\\0\\0" an EXIF block is a small TIFF file:
    - header (8 bytes): byte order "II" (little-endian) or "MM" (big-endian), the number 42,
      and the offset of the first directory (IFD0), counted from the start of the header;
    - IFD0: an entry count (2 bytes), then 12-byte entries: tag (2), type (2), count (4),
      and a 4-byte value field, which holds the value itself when it fits (a SHORT does,
      in its first 2 bytes).
    Orientation is tag 0x0112, type SHORT. Every offset is checked against ``tiff`` (the
    segment's own bytes), and anything malformed gives None rather than an error.
    """
    if len(tiff) < 8:
        return None
    if tiff[:2] == b"II":
        order = "<"
    elif tiff[:2] == b"MM":
        order = ">"
    else:
        return None
    magic, ifd = struct.unpack(order + "HI", tiff[2:8])
    if magic != 42 or ifd < 8 or ifd + 2 > len(tiff):
        return None
    (count,) = struct.unpack(order + "H", tiff[ifd : ifd + 2])
    for index in range(count):
        entry = ifd + 2 + 12 * index
        if entry + 12 > len(tiff):
            return None
        tag, value_type, value_count = struct.unpack(order + "HHI", tiff[entry : entry + 8])
        if tag != _ORIENTATION_TAG:
            continue
        if value_type != _TIFF_SHORT or value_count < 1:
            return None
        (value,) = struct.unpack(order + "H", tiff[entry + 8 : entry + 10])
        return value if 1 <= value <= 8 else None
    return None
