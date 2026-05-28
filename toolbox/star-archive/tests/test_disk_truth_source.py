#!/usr/bin/env python3
"""Regression tests: disk is the single source of truth.

Verifies that finished-state deadlock fixes do not break normal operation.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from backend.services.torrent_engine import _scan_mp4_moov
from backend.services.piece_tracker import PieceStateTracker


class FakeHandle:
    """Minimal mock for libtorrent torrent_handle."""

    def __init__(self, num_pieces: int = 1157, piece_length: int = 1048576):
        self._num_pieces = num_pieces
        self._piece_length = piece_length
        self._prios = [4] * num_pieces
        self._metadata = True
        self._state = "downloading"

    def status(self):
        class S:
            has_metadata = self._metadata
            state = self._state
        return S()

    def torrent_file(self):
        class TF:
            def num_pieces(_):
                return self._num_pieces

            def piece_length(_):
                return self._piece_length

            def files(_):
                class FS:
                    def file_offset(_, idx):
                        return 0

                    def file_size(_, idx):
                        return self._num_pieces * self._piece_length
                return FS()
        return TF()

    def piece_priorities(self):
        return self._prios

    def prioritize_pieces(self, prios):
        self._prios = list(prios)

    def piece_priority(self, p, prio=None):
        if prio is not None:
            self._prios[p] = prio
        return self._prios[p]

    def set_piece_deadline(self, p, deadline):
        pass

    def have_piece(self, p):
        # Simulate finished-state false positive: says True for all pieces
        return True


def test_piece_tracker_no_overlay_have_piece():
    """_overlay_have_piece must not exist — it was the source of finished-state deadlock."""
    assert not hasattr(PieceStateTracker, "_overlay_have_piece")


def test_piece_tracker_bootstrap_is_truth():
    """Tracker state must come from SEEK_HOLE, not have_piece()."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        # Write non-zero data to first 2 pieces exactly, leave rest as holes
        f.write(b"\x01" * (2 * 1048576))
        path = f.name

    try:
        h = FakeHandle(num_pieces=10, piece_length=1048576)
        tracker = PieceStateTracker(
            handle=h,
            video_idx=0,
            video_size=10 * 1048576,
            path=path,
        )
        # have_piece() returns True for ALL pieces (finished false-positive),
        # but tracker must only mark pieces 0-1 as verified because the rest
        # are holes on disk (all zeros / EOF).
        assert tracker.verified_count() == 2, (
            f"expected 2 verified (disk truth), got {tracker.verified_count()}"
        )
        assert tracker.is_verified(0)
        assert tracker.is_verified(1)
        assert not tracker.is_verified(2)
        assert not tracker.is_verified(9)
    finally:
        os.unlink(path)


def test_scan_mp4_moov_on_the_fly():
    """_scan_mp4_moov must find moov even when called after file has data."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        # Minimal tail-moov: some junk + mdat + moov box
        f.write(b"\x00" * 1024)
        mdat_size = 2048
        mdat = mdat_size.to_bytes(4, "big") + b"mdat" + b"\x00" * (mdat_size - 8)
        moov_size = 256
        moov = moov_size.to_bytes(4, "big") + b"moov" + b"\x00" * (moov_size - 8)
        f.write(mdat)
        f.write(moov)
        path = f.name

    try:
        start, end = _scan_mp4_moov(path)
        assert end > 0, "moov must be found"
        assert start > 0, "tail-moov start must be > 0"
        # moov_end should point past the last byte of moov
        assert end == start + moov_size
    finally:
        os.unlink(path)


def test_piece_has_data_on_disk():
    """_piece_has_data_on_disk must reflect actual disk state."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"\x01" * (2 * 1048576))
        path = f.name

    try:
        h = FakeHandle(num_pieces=10, piece_length=1048576)
        tracker = PieceStateTracker(
            handle=h,
            video_idx=0,
            video_size=10 * 1048576,
            path=path,
        )
        assert tracker._piece_has_data_on_disk(0)
        assert tracker._piece_has_data_on_disk(1)
        # Piece 2+ are holes (file ends at exactly 2MB)
        assert not tracker._piece_has_data_on_disk(2)
        assert not tracker._piece_has_data_on_disk(9)
    finally:
        os.unlink(path)
