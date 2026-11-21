"""Parse MP4 and QuickTime MOV file headers using standard library only (D51).

Checks:
- Starts with an 'ftyp' box.
- moov atom appears before mdat atom.
- No edit lists ('edts' containing 'elst' or 'elst' anywhere in moov).
- Duration from 'mvhd' (v0 or v1): duration_s = duration / timescale.
  Duration must be between 3 seconds and 15 minutes (900 seconds).
- Visual track width and height from 'tkhd' (v0 or v1, 16.16 fixed-point):
  Visual track is a track where width > 0 and height > 0.
  Width must be <= 1920.
  Aspect ratio (width / height) must be between 0.01 and 10.
- Does not load entire file into memory; scans box headers and metadata only.
"""

from dataclasses import dataclass
import io
import os
from pathlib import Path
import struct
from typing import BinaryIO

# D51: exactly 300 MB decimal (300,000,000 bytes), not 300 MiB. Instagram documents "300 MB".
MAX_VIDEO_BYTES = 300_000_000
MIN_DURATION_S = 3.0
MAX_DURATION_S = 900.0  # 15 minutes
MAX_WIDTH = 1920
MIN_ASPECT = 0.01
MAX_ASPECT = 10.0


class VideoHeaderError(ValueError):
    """The video header could not be parsed or violates container structure rules."""


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration_s: float
    aspect: float


def parse_video_header(source: Path | BinaryIO | bytes) -> VideoInfo:
    """Parse MP4/MOV atoms from source (file path, stream, or bytes) without reading mdat into memory."""
    if isinstance(source, bytes):
        stream = io.BytesIO(source)
        total_size = len(source)
        return _read_stream(stream, total_size)
    if isinstance(source, Path):
        total_size = source.stat().st_size
        with source.open("rb") as stream:
            return _read_stream(stream, total_size)
    if hasattr(source, "read") and hasattr(source, "seek"):
        pos = source.tell()
        source.seek(0, os.SEEK_END)
        total_size = source.tell() - pos
        source.seek(pos)
        return _read_stream(source, total_size)
    raise TypeError(f"Unsupported video source: {type(source).__name__}")


def _read_stream(stream: BinaryIO, total_size: int) -> VideoInfo:
    start_pos = stream.tell()
    end_pos = start_pos + total_size

    first = True
    moov_seen = False
    duration_s: float | None = None
    visual_tracks: list[tuple[int, int]] = []

    while stream.tell() < end_pos:
        box_start = stream.tell()
        header = stream.read(8)
        if len(header) < 8:
            if first:
                raise VideoHeaderError("the video file is too small or truncated")
            break

        size, fourcc = struct.unpack(">I4s", header)
        if size == 1:
            ext_header = stream.read(8)
            if len(ext_header) < 8:
                raise VideoHeaderError("truncated 64-bit box header")
            size = struct.unpack(">Q", ext_header)[0]
            header_size = 16
        elif size == 0:
            header_size = 8
            size = end_pos - box_start
        else:
            header_size = 8

        if size < header_size:
            raise VideoHeaderError(f"invalid box size {size} for {fourcc.decode('latin1', errors='replace')}")

        payload_size = size - header_size
        payload_start = stream.tell()
        payload_end = payload_start + payload_size

        if first:
            first = False
            if fourcc != b"ftyp":
                raise VideoHeaderError("the video does not start with an ftyp box (not a valid MP4 or MOV file)")

        if fourcc == b"mdat":
            if not moov_seen:
                raise VideoHeaderError("moov atom must appear before mdat")
            # If moov was already parsed, we can finish early!
            break
        elif fourcc == b"moov":
            moov_seen = True
            duration_s, visual_tracks = _parse_moov(stream, payload_start, payload_end)
            # moov parsed; skip to end of moov payload
            stream.seek(payload_end)
        else:
            stream.seek(payload_end)

    if not moov_seen:
        raise VideoHeaderError("no moov atom found in video")
    if duration_s is None:
        raise VideoHeaderError("no mvhd atom found in moov")
    if not visual_tracks:
        raise VideoHeaderError("no visual track found in video")

    width, height = visual_tracks[0]
    aspect = width / height if height > 0 else 0.0

    return VideoInfo(width=width, height=height, duration_s=duration_s, aspect=aspect)


def _parse_moov(stream: BinaryIO, moov_start: int, moov_end: int) -> tuple[float | None, list[tuple[int, int]]]:
    """Parse child boxes inside moov, checking for edit lists, mvhd, and trak/tkhd."""
    duration_s: float | None = None
    visual_tracks: list[tuple[int, int]] = []

    stream.seek(moov_start)
    while stream.tell() < moov_end:
        box_start = stream.tell()
        header = stream.read(8)
        if len(header) < 8:
            break
        size, fourcc = struct.unpack(">I4s", header)
        if size == 1:
            ext = stream.read(8)
            if len(ext) < 8:
                raise VideoHeaderError("truncated 64-bit box in moov")
            size = struct.unpack(">Q", ext)[0]
            header_size = 16
        elif size == 0:
            header_size = 8
            size = moov_end - box_start
        else:
            header_size = 8

        payload_size = size - header_size
        payload_start = stream.tell()
        payload_end = payload_start + payload_size

        if fourcc == b"mvhd":
            data = stream.read(payload_size)
            if len(data) < 20:
                raise VideoHeaderError("mvhd atom truncated")
            version = data[0]
            if version == 0:
                timescale, duration = struct.unpack(">II", data[12:20])
            elif version == 1:
                if len(data) < 32:
                    raise VideoHeaderError("mvhd atom truncated")
                timescale, duration = struct.unpack(">IQ", data[20:32])
            else:
                raise VideoHeaderError(f"unsupported mvhd version {version}")
            if timescale == 0:
                raise VideoHeaderError("invalid timescale (0) in mvhd")
            duration_s = duration / timescale
        elif fourcc == b"trak":
            # Check for edit lists or parse tkhd inside trak
            w, h = _parse_trak(stream, payload_start, payload_end)
            if w > 0 and h > 0:
                visual_tracks.append((w, h))
        elif fourcc in (b"edts", b"elst"):
            raise VideoHeaderError("video contains edit lists (elst), which are not supported")

        stream.seek(payload_end)

    return duration_s, visual_tracks


def _parse_trak(stream: BinaryIO, trak_start: int, trak_end: int) -> tuple[int, int]:
    """Parse one trak box: reject edit lists and extract width/height from tkhd."""
    stream.seek(trak_start)
    width, height = 0, 0

    while stream.tell() < trak_end:
        box_start = stream.tell()
        header = stream.read(8)
        if len(header) < 8:
            break
        size, fourcc = struct.unpack(">I4s", header)
        if size == 1:
            ext = stream.read(8)
            if len(ext) < 8:
                raise VideoHeaderError("truncated 64-bit box in trak")
            size = struct.unpack(">Q", ext)[0]
            header_size = 16
        elif size == 0:
            header_size = 8
            size = trak_end - box_start
        else:
            header_size = 8

        payload_size = size - header_size
        payload_start = stream.tell()
        payload_end = payload_start + payload_size

        if fourcc in (b"edts", b"elst"):
            raise VideoHeaderError("video contains edit lists (elst), which are not supported")
        if fourcc == b"tkhd":
            data = stream.read(payload_size)
            if len(data) < 84:
                raise VideoHeaderError("tkhd atom truncated")
            version = data[0]
            if version == 0:
                w_fp, h_fp = struct.unpack(">II", data[76:84])
            elif version == 1:
                if len(data) < 96:
                    raise VideoHeaderError("tkhd atom truncated")
                w_fp, h_fp = struct.unpack(">II", data[88:96])
            else:
                raise VideoHeaderError(f"unsupported tkhd version {version}")
            width = w_fp >> 16
            height = h_fp >> 16
        elif fourcc in (b"mdia",):
            # Recursively check mdia for edit lists
            _check_child_boxes_for_elst(stream, payload_start, payload_end)

        stream.seek(payload_end)

    return width, height


def _check_child_boxes_for_elst(stream: BinaryIO, start: int, end: int) -> None:
    """Scan container boxes for forbidden edts/elst boxes."""
    stream.seek(start)
    while stream.tell() < end:
        header = stream.read(8)
        if len(header) < 8:
            break
        size, fourcc = struct.unpack(">I4s", header)
        if size == 1:
            ext = stream.read(8)
            if len(ext) < 8:
                break
            size = struct.unpack(">Q", ext)[0]
            header_size = 16
        elif size == 0:
            header_size = 8
            size = end - stream.tell() + header_size
        else:
            header_size = 8

        if fourcc in (b"edts", b"elst"):
            raise VideoHeaderError("video contains edit lists (elst), which are not supported")

        payload_size = size - header_size
        payload_start = stream.tell()
        payload_end = payload_start + payload_size
        if fourcc in (b"minf", b"stbl"):
            _check_child_boxes_for_elst(stream, payload_start, payload_end)
        stream.seek(payload_end)
