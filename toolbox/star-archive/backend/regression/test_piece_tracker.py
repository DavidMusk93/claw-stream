"""Regression tests for PieceStateTracker."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import sys

# Ensure backend is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.services.piece_tracker import PieceState, PieceStateTracker


class FakeTorrentInfo:
    """Minimal mock of libtorrent.torrent_info."""

    def __init__(self, num_pieces: int = 100, piece_length: int = 262144) -> None:
        self._num_pieces = num_pieces
        self._piece_length = piece_length
        self._files = FakeFileStorage(num_pieces * piece_length)

    def num_pieces(self) -> int:
        return self._num_pieces

    def piece_length(self) -> int:
        return self._piece_length

    def files(self):
        return self._files


class FakeFileStorage:
    """Minimal mock of libtorrent.file_storage."""

    def __init__(self, total_size: int) -> None:
        self._total_size = total_size

    def num_files(self) -> int:
        return 1

    def file_offset(self, idx: int) -> int:
        return 0

    def file_size(self, idx: int) -> int:
        return self._total_size


class FakeHandle:
    """Minimal mock of libtorrent.torrent_handle."""

    def __init__(self, have_pieces: set[int] | None = None) -> None:
        self._have = have_pieces or set()
        self._priorities: dict[int, int] = {}
        self._deadlines: dict[int, int] = {}
        self._ti = FakeTorrentInfo()

    def torrent_file(self):
        return self._ti

    def have_piece(self, p: int) -> bool:
        return p in self._have

    def set_piece_deadline(self, p: int, deadline: int) -> None:
        self._deadlines[p] = deadline

    def piece_priority(self, p: int, prio: int | None = None) -> int | None:
        if prio is not None:
            self._priorities[p] = prio
            return None
        return self._priorities.get(p, 4)

    def info_hash(self):
        return MagicMock()

    def status(self):
        return MagicMock(save_path="/tmp")

    def prioritize_files(self, prios: list[int]) -> None:
        pass

    def prioritize_pieces(self, prios: list[int]) -> None:
        pass

    def set_sequential_download(self, val: bool) -> None:
        pass


def make_tracker(
    have_pieces: set[int] | None = None,
    video_size: int = 10 * 262144,  # 10 pieces
) -> PieceStateTracker:
    """Create a tracker with mocked handle and a dummy file."""
    handle = FakeHandle(have_pieces=have_pieces)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        # Write some non-zero data so SEEK_HOLE sees data
        f.write(b"\x01" * video_size)
        path = f.name

    try:
        with patch.object(PieceStateTracker, "_bootstrap_from_filesystem", lambda self: None):
            tracker = PieceStateTracker(
                handle=handle,
                video_idx=0,
                video_size=video_size,
                path=path,
            )
        return tracker
    finally:
        os.unlink(path)


class TestOverlayHavePiece(unittest.TestCase):
    """Tests for _overlay_have_piece strict mode."""

    def test_incremental_only_adds_verified(self) -> None:
        """Default mode: only NOT_DOWNLOADED -> VERIFIED, never resets."""
        tracker = make_tracker(have_pieces={2, 3})
        tracker._states[2] = PieceState.NOT_DOWNLOADED
        tracker._states[3] = PieceState.NOT_DOWNLOADED
        tracker._states[4] = PieceState.VERIFIED  # already verified
        tracker._states[5] = PieceState.CORRUPT   # corrupt stays

        tracker._overlay_have_piece(strict=False)

        self.assertEqual(tracker._states[2], PieceState.VERIFIED)
        self.assertEqual(tracker._states[3], PieceState.VERIFIED)
        self.assertEqual(tracker._states[4], PieceState.VERIFIED)
        self.assertEqual(tracker._states[5], PieceState.CORRUPT)

    def test_strict_resets_false_verified(self) -> None:
        """Strict mode: have_piece=false forces VERIFIED -> NOT_DOWNLOADED.

        This is the key regression test for the recheck zero-hole bug:
        _bootstrap_from_filesystem falsely marks zero-filled blocks as
        VERIFIED because SEEK_HOLE cannot distinguish zeros from data.
        After recheck, have_piece() is authoritative; strict mode must
        override the filesystem scan.
        """
        tracker = make_tracker(have_pieces={2})  # only piece 2 is truly valid
        # Simulate _bootstrap_from_filesystem falsely marking pieces 3-4
        tracker._states[2] = PieceState.VERIFIED   # correct
        tracker._states[3] = PieceState.VERIFIED   # false positive (zeros on disk)
        tracker._states[4] = PieceState.VERIFIED   # false positive
        tracker._states[5] = PieceState.DOWNLOADING  # should not be touched
        tracker._states[6] = PieceState.CORRUPT      # should not be touched

        tracker._overlay_have_piece(strict=True)

        self.assertEqual(tracker._states[2], PieceState.VERIFIED)
        self.assertEqual(tracker._states[3], PieceState.NOT_DOWNLOADED)
        self.assertEqual(tracker._states[4], PieceState.NOT_DOWNLOADED)
        self.assertEqual(tracker._states[5], PieceState.DOWNLOADING)
        self.assertEqual(tracker._states[6], PieceState.CORRUPT)

    def test_strict_does_not_affect_not_downloaded(self) -> None:
        """Strict mode should not change NOT_DOWNLOADED pieces."""
        tracker = make_tracker(have_pieces=set())
        tracker._states[2] = PieceState.NOT_DOWNLOADED

        tracker._overlay_have_piece(strict=True)

        self.assertEqual(tracker._states[2], PieceState.NOT_DOWNLOADED)


class TestRequestPieces(unittest.TestCase):
    """Tests for request_pieces behaviour."""

    def test_request_pieces_skips_verified(self) -> None:
        """Verified pieces should not be re-requested (regression for requested=0 bug)."""
        tracker = make_tracker(have_pieces={2, 3, 4})
        tracker._states[2] = PieceState.VERIFIED
        tracker._states[3] = PieceState.VERIFIED
        tracker._states[4] = PieceState.NOT_DOWNLOADED

        requested = tracker.request_pieces(2, 4)

        self.assertEqual(requested, 1)  # only piece 4
        self.assertEqual(tracker._states[4], PieceState.DOWNLOADING)
        self.assertEqual(tracker.handle._deadlines[4], 0)

    def test_request_pieces_includes_corrupt(self) -> None:
        """Corrupt pieces should be re-requested."""
        tracker = make_tracker()
        tracker._states[2] = PieceState.CORRUPT

        requested = tracker.request_pieces(2, 2)

        self.assertEqual(requested, 1)
        self.assertEqual(tracker._states[2], PieceState.DOWNLOADING)


class TestHeadReady(unittest.TestCase):
    """Tests for head_ready query."""

    def test_head_ready_all_verified(self) -> None:
        tracker = make_tracker()
        tracker._states[0] = PieceState.VERIFIED
        tracker._states[1] = PieceState.VERIFIED
        tracker._states[2] = PieceState.VERIFIED

        self.assertTrue(tracker.head_ready(moov_start=0, moov_end=524288))

    def test_head_ready_missing_piece(self) -> None:
        tracker = make_tracker()
        tracker._states[0] = PieceState.VERIFIED
        tracker._states[1] = PieceState.NOT_DOWNLOADED
        tracker._states[2] = PieceState.VERIFIED

        self.assertFalse(tracker.head_ready(moov_start=0, moov_end=524288))


if __name__ == "__main__":
    unittest.main()
