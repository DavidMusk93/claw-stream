#!/usr/bin/env python3
"""Shared fixtures for star-archive regression tests.

Generates a synthetic tail-moov sparse MP4 file so tail-moov tests
never depend on downloading a 5.5GB real torrent.
"""
from __future__ import annotations

import os
import tempfile

import pytest


def _build_moov_box(duration_sec: int = 60) -> bytes:
    """Build a minimal valid moov box (ftyp/isom compatible, no actual streams).

    Contains mvhd + trak + mdia + minf + stbl with one video track.
    This is enough for _scan_mp4_moov and _range_has_data tests.
    """
    def box(btype: bytes, data: bytes) -> bytes:
        size = 8 + len(data)
        return size.to_bytes(4, "big") + btype + data

    # stsd (sample description) — empty entry count
    stsd = box(b"stsd", (1).to_bytes(4, "big"))
    # stts (time to sample)
    stts = box(b"stts", (0).to_bytes(4, "big"))
    # stsc (sample to chunk)
    stsc = box(b"stsc", (0).to_bytes(4, "big"))
    # stsz (sample size)
    stsz = box(b"stsz", (0).to_bytes(4, "big") + (0).to_bytes(4, "big"))
    # stco (chunk offset)
    stco = box(b"stco", (0).to_bytes(4, "big"))
    stbl = box(b"stbl", stsd + stts + stsc + stsz + stco)

    # dinf (data information) — minimal dref
    dref = box(b"dref", (0).to_bytes(4, "big") + (1).to_bytes(4, "big"))
    dinf = box(b"dinf", dref)
    minf = box(b"minf", dinf + stbl)

    # hdlr (handler)
    hdlr = box(b"hdlr", (0).to_bytes(4, "big") + b"vide" + b"\x00" * 12 + b"VideoHandler\x00")
    mdia = box(b"mdia", hdlr + minf)

    # tkhd (track header)
    tkhd = box(b"tkhd", (0x00000007).to_bytes(4, "big") + (1).to_bytes(4, "big") + (0).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x00" * 8 + (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + b"\x00" * 36 + (1920).to_bytes(4, "big") + (1080).to_bytes(4, "big"))
    trak = box(b"trak", tkhd + mdia)

    # mvhd (movie header)
    timescale = 1000
    mvhd = box(b"mvhd", (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + timescale.to_bytes(4, "big") + (duration_sec * timescale).to_bytes(4, "big") + (0x00010000).to_bytes(4, "big") + (0x0100).to_bytes(2, "big") + b"\x00" * 10 + b"\x00" * 36 + b"\x00" * 24 + (2).to_bytes(4, "big") + (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + (1).to_bytes(4, "big") + (0).to_bytes(4, "big") + (1).to_bytes(4, "big") + (0).to_bytes(4, "big") + (1).to_bytes(4, "big") + (0).to_bytes(4, "big") + (0x40000000).to_bytes(4, "big") + b"\x00" * 24 + b"\x00" * 4)

    moov = box(b"moov", mvhd + trak)
    return moov


def _write_tail_moov_sparse(path: str, logic_size: int = 32 * 1024 * 1024) -> None:
    """Write a synthetic tail-moov MP4 sparse file.

    Layout: [ftyp][mdat][hole...hole][moov]
    The hole region is created via ftruncate (sparse on ext4).
    """
    moov = _build_moov_box()
    moov_size = len(moov)

    ftyp_data = b"isom" + (0x200).to_bytes(4, "big") + b"isom" + b"mp41"
    ftyp = (8 + len(ftyp_data)).to_bytes(4, "big") + b"ftyp" + ftyp_data

    # mdat fills the gap between ftyp and moov
    mdat_total_size = logic_size - len(ftyp) - moov_size
    mdat = mdat_total_size.to_bytes(4, "big") + b"mdat"

    with open(path, "wb") as f:
        f.write(ftyp)
        f.write(mdat)
        f.flush()
        # Truncate to create sparse hole, leaving room for moov at tail
        os.ftruncate(f.fileno(), logic_size - moov_size)
        f.seek(logic_size - moov_size)
        f.write(moov)


@pytest.fixture(scope="session")
def synthetic_tail_moov(tmp_path_factory):
    """Provide a synthetic tail-moov sparse MP4 file for tail-moov tests."""
    tmp_dir = tmp_path_factory.mktemp("tail_moov")
    path = str(tmp_dir / "synthetic_tail_moov.mp4")
    _write_tail_moov_sparse(path)
    return path


@pytest.fixture(scope="session")
def synthetic_tail_moov_dir(tmp_path_factory):
    """Provide a directory that mimics a torrent cache dir with synthetic tail-moov file."""
    tmp_dir = tmp_path_factory.mktemp("torrent_cache")
    hash_dir = tmp_dir / "synth_tail_moov_hash"
    hash_dir.mkdir()
    video_dir = hash_dir / "SYNTH-001"
    video_dir.mkdir()
    path = str(video_dir / "hhd800.com@SYNTH-001.mp4")
    _write_tail_moov_sparse(path)
    return str(hash_dir), path
