"""Regression tests for torrent_engine re-add behaviour."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.services.torrent_engine import TorrentEngine


class FakeTorrentInfo:
    def __init__(self, name: str = "hhd800.com@test.mp4", num_pieces: int = 100, piece_length: int = 262144) -> None:
        self._name = name
        self._num_pieces = num_pieces
        self._piece_length = piece_length
        self._files = FakeFileStorage(num_pieces * piece_length, name)

    def name(self) -> str:
        return self._name

    def num_pieces(self) -> int:
        return self._num_pieces

    def piece_length(self) -> int:
        return self._piece_length

    def files(self):
        return self._files

    def info_section(self):
        return b"info"


class FakeFileStorage:
    def __init__(self, total_size: int, name: str = "hhd800.com@test.mp4") -> None:
        self._total_size = total_size
        self._name = name

    def num_files(self) -> int:
        return 1

    def file_path(self, idx: int) -> str:
        return self._name

    def file_size(self, idx: int) -> int:
        return self._total_size

    def file_offset(self, idx: int) -> int:
        return 0


class FakeInfoHash:
    def __init__(self, hash_str: str) -> None:
        self._hash = hash_str
    def __str__(self) -> str:
        return self._hash


class FakeHandle:
    """Mock libtorrent handle with spyable priority calls."""

    def __init__(self, have_pieces: set[int] | None = None, state=None, hash_str: str = "a" * 40) -> None:
        self._have = have_pieces or set()
        self._piece_prios: list[int] = []
        self._file_prios: list[int] = []
        self._deadlines: dict[int, int] = {}
        self._ti = FakeTorrentInfo()
        self._state = state or MagicMock()
        self._ih = FakeInfoHash(hash_str)

    def torrent_file(self):
        return self._ti

    def have_piece(self, p: int) -> bool:
        return p in self._have

    def set_piece_deadline(self, p: int, deadline: int) -> None:
        self._deadlines[p] = deadline

    def piece_priority(self, p: int, prio: int | None = None) -> int | None:
        if prio is not None:
            while len(self._piece_prios) <= p:
                self._piece_prios.append(4)
            self._piece_prios[p] = prio
            return None
        if p < len(self._piece_prios):
            return self._piece_prios[p]
        return 4

    def prioritize_pieces(self, prios: list[int]) -> None:
        self._piece_prios = list(prios)

    def prioritize_files(self, prios: list[int]) -> None:
        self._file_prios = list(prios)

    def info_hash(self):
        return self._ih

    def status(self):
        return self._state

    def set_sequential_download(self, val: bool) -> None:
        pass

    def force_recheck(self) -> None:
        pass


class TestOnMetadataReadd(unittest.TestCase):
    """Regression: repeated add_torrent must not reset piece priorities."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.engine = TorrentEngine(
            cache_dir=self.tmpdir,
            max_size_gb=1,
        )
        # Stub out alert thread
        self.engine._alert_thread_running = False

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_torrent_info(self, hash_str: str = "a" * 40) -> dict:
        """Create a minimal torrent entry in engine.torrents."""
        handle = FakeHandle()
        info = {
            "hash": hash_str,
            "handle": handle,
            "prefetch": False,
            "last_access": 0.0,
            "ready": False,
            "video_idx": 0,
            "video_path": os.path.join(self.tmpdir, hash_str, "test.mp4"),
            "video_size": 100 * 262144,
        }
        self.engine.torrents[hash_str] = info
        # Create dummy file so _bootstrap_from_filesystem doesn't crash
        os.makedirs(os.path.dirname(info["video_path"]), exist_ok=True)
        with open(info["video_path"], "wb") as f:
            f.write(b"\x01" * info["video_size"])
        return info

    def test_first_metadata_sets_play_priority(self) -> None:
        """First _on_metadata should set piece priorities and flag."""
        info = self._make_torrent_info()
        handle = info["handle"]

        self.assertFalse(info.get("_play_priority_applied"))

        self.engine._on_metadata(handle)

        self.assertTrue(info.get("_play_priority_applied"))
        # Head pieces should be priority 7
        self.assertEqual(handle._piece_prios[0], 7)
        self.assertEqual(handle._piece_prios[1], 7)
        # Some middle piece should be 0 (outside head+tail window)
        self.assertEqual(handle._piece_prios[50], 0)

    def test_second_metadata_does_not_reset_priorities(self) -> None:
        """Regression: second _on_metadata must NOT reset piece_prios to all-0.

        Before the fix, _on_metadata unconditionally executed:
            piece_prios = [0] * num_pieces
            handle.prioritize_pieces(piece_prios)
        even when _play_priority_applied was already True. This caused
        the sliding window to be wiped every time the frontend polled
        /torrent/add, driving libtorrent into finished state with no peers.
        """
        info = self._make_torrent_info()
        handle = info["handle"]

        # First call — sets priorities
        self.engine._on_metadata(handle)
        self.assertTrue(info.get("_play_priority_applied"))
        first_prios = list(handle._piece_prios)
        self.assertEqual(first_prios[0], 7)

        # Simulate frontend re-adding: second _on_metadata call
        self.engine._on_metadata(handle)

        # Priorities must NOT be reset to all-0
        self.assertEqual(handle._piece_prios[0], 7,
                         "piece 0 priority was reset on re-add")
        self.assertNotEqual(handle._piece_prios, [0] * 100,
                            "piece priorities were fully reset on re-add")

    def test_file_priorities_still_set_on_readd(self) -> None:
        """File priority for the video file must still be set on re-add."""
        info = self._make_torrent_info()
        handle = info["handle"]

        self.engine._on_metadata(handle)
        self.assertEqual(handle._file_prios, [4])

        handle._file_prios = []
        self.engine._on_metadata(handle)
        self.assertEqual(handle._file_prios, [4])


if __name__ == "__main__":
    unittest.main()
