"""Read an image's width and height from its file header, without decoding it.

LinkedIn accepts images with "less than 36,152,320 pixels". Rather than adding an
imaging library, the size is read from the header with the standard library:

- PNG: the IHDR chunk, which must come first, right after the 8-byte signature.
- JPEG: the start-of-frame (SOF) segment. It is not at a fixed place: segments
  such as EXIF (APP1), JFIF (APP0), quantization and Huffman tables can come
  before it, so the segments are walked one by one until a SOF marker appears.

An image whose size cannot be read is refused rather than guessed.
"""

import struct

# LinkedIn: "Images with less than 36,152,320 pixels".
MAX_PIXELS = 36_152_320

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_START = b"\xff\xd8"

# Start-of-frame markers carry the size: C0-CF, except C4 (Huffman tables), C8 (reserved), CC (arithmetic coding).
_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers with no length field: TEM, RST0-RST7. (SOI and EOI never appear between segments.)
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_START_OF_SCAN = 0xDA


class ImageSizeError(ValueError):
    """The width and height could not be read from the image header."""


def image_size(data: bytes) -> tuple[int, int]:
    """``(width, height)`` of a PNG or JPEG image, read from its header."""
    if data.startswith(PNG_SIGNATURE):
        return _png_size(data)
    if data.startswith(JPEG_START):
        return _jpeg_size(data)
    raise ImageSizeError("not a PNG or JPEG file")


def image_problem(data: bytes) -> str | None:
    """Why LinkedIn would not accept this image's size, or None."""
    try:
        width, height = image_size(data)
    except ImageSizeError as error:
        return f"image size could not be read ({error})"
    if width == 0 or height == 0:
        return f"image size is {width} x {height} pixels"
    if width * height >= MAX_PIXELS:
        return (
            f"image is {width} x {height} = {width * height:,} pixels; "
            f"LinkedIn accepts less than {MAX_PIXELS:,}"
        )
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    # Signature (8), then the first chunk: length (4), type (4), width (4), height (4).
    if len(data) < 24:
        raise ImageSizeError("PNG header is truncated")
    if data[12:16] != b"IHDR":
        raise ImageSizeError("PNG does not start with an IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int]:
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
        if marker in _SOF_MARKERS:
            # Length (2), precision (1), height (2), width (2).
            if length < 7 or position + 7 > len(data):
                raise ImageSizeError("JPEG size segment is truncated")
            height, width = struct.unpack(">HH", data[position + 3 : position + 7])
            return width, height
        position += length
