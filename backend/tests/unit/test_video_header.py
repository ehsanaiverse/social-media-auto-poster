"""Tests for MP4/MOV header parsing and validation (stdlib only, D51).

Tests on fakes only (tiny MP4 atoms built in tests).
Checks:
- Valid MP4 and MOV headers.
- Top-level atoms: ftyp must be first, moov must appear before mdat.
- mvhd: duration between 3 seconds and 15 minutes (v0 and v1).
- tkhd: visual track dimensions, width <= 1920, aspect ratio between 0.01 and 10.
- No edit lists (elst/edts forbidden).
- Max video size 300 MB (exactly 300,000,000 bytes; the tests pin the boundary).
"""

from pathlib import Path
import pytest

from social_media_auto_poster.platforms.video_header import (
    MAX_ASPECT,
    MAX_DURATION_S,
    MAX_VIDEO_BYTES,
    MAX_WIDTH,
    MIN_ASPECT,
    MIN_DURATION_S,
    VideoHeaderError,
    VideoInfo,
    parse_video_header,
)
from social_media_auto_poster.platforms.instagram.rules import video_problems
from video_fake import box, build_mp4, make_mvhd, make_tkhd


def test_valid_mp4_parses_cleanly():
    data = build_mp4(width=1080, height=1920, duration=10000, timescale=1000)
    info = parse_video_header(data)
    assert info.width == 1080
    assert info.height == 1920
    assert info.duration_s == 10.0
    assert round(info.aspect, 4) == round(1080 / 1920, 4)
    assert video_problems(data) == []


def test_valid_mp4_version_1():
    data = build_mp4(width=720, height=1280, duration=60000, timescale=1000, version=1)
    info = parse_video_header(data)
    assert info.width == 720
    assert info.height == 1280
    assert info.duration_s == 60.0
    assert video_problems(data) == []


def test_quicktime_mov_ftyp():
    qt_ftyp = box(b"ftyp", b"qt  \x00\x00\x02\x00qt  ")
    data = build_mp4(ftyp=qt_ftyp)
    info = parse_video_header(data)
    assert info.width == 1080
    assert video_problems(data) == []


def test_missing_ftyp_rejected():
    # File starts directly with moov
    mvhd = make_mvhd()
    tkhd = make_tkhd()
    moov = box(b"moov", mvhd + box(b"trak", tkhd))
    with pytest.raises(VideoHeaderError, match="ftyp"):
        parse_video_header(moov)


def test_moov_after_mdat_rejected():
    data = build_mp4(mdat_before_moov=True)
    with pytest.raises(VideoHeaderError, match="moov atom must appear before mdat"):
        parse_video_header(data)
    problems = video_problems(data)
    assert any("moov atom must appear before mdat" in p for p in problems)


def test_edit_lists_rejected():
    data = build_mp4(with_elst=True)
    with pytest.raises(VideoHeaderError, match="edit lists"):
        parse_video_header(data)
    problems = video_problems(data)
    assert any("edit lists" in p for p in problems)


def test_duration_under_3_seconds_rejected():
    data = build_mp4(duration=2000, timescale=1000)  # 2.0 seconds
    info = parse_video_header(data)
    assert info.duration_s == 2.0
    problems = video_problems(data)
    assert any("duration is 2.0 s; Instagram allows 3 s to 15 min" in p for p in problems)


def test_duration_over_15_minutes_rejected():
    data = build_mp4(duration=901000, timescale=1000)  # 901.0 seconds = 15m 1s
    info = parse_video_header(data)
    assert info.duration_s == 901.0
    problems = video_problems(data)
    assert any("duration is 901.0 s; Instagram allows 3 s to 15 min" in p for p in problems)


def test_width_over_1920_rejected():
    data = build_mp4(width=1921, height=1080)
    info = parse_video_header(data)
    assert info.width == 1921
    problems = video_problems(data)
    assert any("video width is 1921" in p for p in problems)


def test_aspect_ratio_out_of_bounds_rejected():
    # 0.01 to 10
    narrow = build_mp4(width=10, height=2000)  # aspect 0.005 < 0.01
    problems_narrow = video_problems(narrow)
    assert any("aspect ratio is 0.01" in p or "outside 0.01 to 10" in p for p in problems_narrow)

    wide = build_mp4(width=1920, height=100)  # aspect 19.2 > 10
    problems_wide = video_problems(wide)
    assert any("aspect ratio is 19.20" in p or "outside 0.01 to 10" in p for p in problems_wide)


def test_video_limit_is_decimal_300_mb():
    # "300 MB" means 300,000,000 bytes, not 300 MiB (314,572,800), all the way down to the constant.
    assert MAX_VIDEO_BYTES == 300_000_000


def test_file_size_at_exactly_300_mb_accepted(tmp_path: Path):
    video_file = tmp_path / "reel.mp4"
    video_file.write_bytes(build_mp4())
    assert video_problems(video_file, file_size=MAX_VIDEO_BYTES) == []


def test_file_size_one_byte_over_300_mb_rejected(tmp_path: Path):
    video_file = tmp_path / "reel.mp4"
    video_file.write_bytes(build_mp4())
    problems = video_problems(video_file, file_size=MAX_VIDEO_BYTES + 1)
    assert any("larger than 300,000,000 bytes (300 MB)" in p for p in problems)
