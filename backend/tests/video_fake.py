"""Tiny MP4/QuickTime files built atom by atom, for the video header and Reel tests (D51).

They are containers only (no real codec data), which is all the atom checks read.
Each helper returns bytes, so a test can pass them in memory or write them to a file.
"""

import struct


def box(fourcc: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + fourcc + payload


def make_mvhd(*, duration: int = 10000, timescale: int = 1000, version: int = 0) -> bytes:
    if version == 0:
        body = struct.pack(">BBBBIIII", 0, 0, 0, 0, 0, 0, timescale, duration) + b"\x00" * 80
    else:
        body = struct.pack(">BBBBQQIQ", 1, 0, 0, 0, 0, 0, timescale, duration) + b"\x00" * 80
    return box(b"mvhd", body)


def make_tkhd(*, width: int = 1080, height: int = 1920, version: int = 0) -> bytes:
    if version == 0:
        body = struct.pack(">BBBBIIIII", 0, 0, 0, 1, 0, 0, 1, 0, 10000)
        body += b"\x00" * 52
        body += struct.pack(">II", width << 16, height << 16)
    else:
        body = struct.pack(">BBBBQQIIQ", 1, 0, 0, 1, 0, 0, 1, 0, 10000)
        body += b"\x00" * 52
        body += struct.pack(">II", width << 16, height << 16)
    return box(b"tkhd", body)


def build_mp4(
    *,
    width: int = 1080,
    height: int = 1920,
    duration: int = 10000,
    timescale: int = 1000,
    ftyp: bytes | None = None,
    with_mdat: bool = True,
    mdat_before_moov: bool = False,
    with_elst: bool = False,
    version: int = 0,
) -> bytes:
    if ftyp is None:
        ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41")
    mvhd = make_mvhd(duration=duration, timescale=timescale, version=version)
    tkhd = make_tkhd(width=width, height=height, version=version)
    trak_children = [tkhd]
    if with_elst:
        elst = box(b"elst", b"\x00" * 16)
        edts = box(b"edts", elst)
        trak_children.append(edts)
    trak = box(b"trak", b"".join(trak_children))
    moov = box(b"moov", mvhd + trak)
    mdat = box(b"mdat", b"\x00" * 32) if with_mdat else b""

    if mdat_before_moov:
        return ftyp + mdat + moov
    return ftyp + moov + mdat
