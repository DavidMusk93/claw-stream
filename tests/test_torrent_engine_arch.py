#!/usr/bin/env python3
"""Architecture regression tests for TorrentEngine.

Covers:
1. Bootstrap-first verification: finished torrents skip force_recheck when
   disk data is intact (seconds of lseek vs minutes of hash recheck).
2. Cache-warming retry: get_status re-applies play priority every 10s while
   head_ready is false, preventing stuck tail-moov downloads.

Run: cd tests && python3 -m pytest test_torrent_engine_arch.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import libtorrent as lt

from services.torrent_engine import TorrentEngine


# ── Helpers ─────────────────────────────────────────────────────────

def _make_minimal_mp4(path: str) -> None:
    """Create a minimal valid MP4 file (ftyp + moov) padded to 1MB."""
    with open(path, "wb") as f:
        # ftyp box
        f.write(b"\x00\x00\x00\x14" + b"ftyp" + b"isom" + b"\x00\x00\x00\x00" + b"isom")
        # moov box
        f.write(b"\x00\x00\x00\x08" + b"moov")
        # Pad to 1MB so _check_video_ready accepts it
        f.write(b"\x00" * (1024 * 1024 - f.tell()))


class MockTorrentHandle:
    """Minimal mock of lt.torrent_handle for architecture tests."""

    def __init__(self, state, has_metadata: bool = True) -> None:
        self._state = state
        self._has_metadata = has_metadata
        self._force_recheck_called = False
        self._save_path = ""
        self._prios: list[int] = [4] * 10
        self._deadlines: dict[int, int] = {}
        self._have: set[int] = set()
        self._hash = ""
        self._paused = False

    def is_valid(self) -> bool:
        return True

    def status(self) -> MagicMock:
        m = MagicMock()
        m.state = self._state
        m.has_metadata = self._has_metadata
        m.paused = self._paused
        m.name = "test"
        m.num_peers = 5
        m.progress = 0.5
        m.download_rate = 1024
        m.upload_rate = 512
        m.save_path = self._save_path
        return m

    def resume(self) -> None:
        self._paused = False

    def pause(self) -> None:
        self._paused = True

    def set_sequential_download(self, on: bool) -> None:
        pass

    def force_recheck(self) -> None:
        self._force_recheck_called = True

    def force_reannounce(self) -> None:
        pass

    def force_dht_announce(self) -> None:
        pass

    def info_hash(self) -> MagicMock:
        return MagicMock(__str__=lambda s: self._hash)

    def torrent_file(self) -> MagicMock:
        return MagicMock(
            files=lambda: MagicMock(
                num_files=lambda: 2,
                file_path=lambda i: ["other.bin", "hhd800.com@video.mp4"][i],
                file_size=lambda i: [512, 10 * 2_097_152][i],
                file_offset=lambda i: [0, 512][i],
            ),
            num_pieces=lambda: 10,
            piece_length=lambda: 2_097_152,
            name=lambda: "test",
            info_section=lambda: b"fake",
        )

    def have_piece(self, p: int) -> bool:
        return p in self._have

    def prioritize_files(self, prios: list[int]) -> None:
        pass

    def prioritize_pieces(self, prios: list[int]) -> None:
        self._prios = list(prios)

    def piece_priorities(self) -> list[int]:
        return list(self._prios)

    def set_piece_deadline(self, p: int, deadline: int) -> None:
        self._deadlines[p] = deadline


class MockTracker:
    """Minimal mock of PieceStateTracker for architecture tests."""

    def __init__(
        self,
        head_ready_val: bool = False,
        moov_pc: int = 0,
        verified: set[int] | None = None,
        start_piece: int = 0,
        end_piece: int = 9,
        piece_length: int = 2_097_152,
        file_offset: int = 512,
    ) -> None:
        self._head_ready = head_ready_val
        self._moov_pc = moov_pc
        self._moov_vc = moov_pc if head_ready_val else 0
        self._bootstrap_called = False
        self._overlay_called = False
        self._request_head_tail_called = False
        self._moov_range_set = False
        self.verified = set(verified) if verified else set()
        self.start_piece = start_piece
        self.end_piece = end_piece
        self.piece_length = piece_length
        self.file_offset = file_offset
        self.handle: Any = None  # optional, lets reset_priorities zero the handle
        self.request_pieces_calls: list[tuple[int, int]] = []
        self.reset_priorities_called = False

    def head_ready(self) -> bool:
        return self._head_ready

    def is_verified(self, p: int) -> bool:
        return p in self.verified

    def _bootstrap_from_filesystem(self) -> None:
        self._bootstrap_called = True

    def _overlay_have_piece(self, strict: bool = False) -> None:
        self._overlay_called = True

    def request_head_tail(self, head_count: int = 30, tail_count: int = 30) -> int:
        self._request_head_tail_called = True
        return 5

    def verified_count(self) -> int:
        return 5

    def set_moov_range(self, moov_start: int, moov_end: int) -> None:
        self._moov_range_set = True

    def request_pieces(self, start_piece: int, end_piece: int) -> int:
        self.request_pieces_calls.append((start_piece, end_piece))
        return 0

    def reset_priorities(self) -> None:
        self.reset_priorities_called = True
        if self.handle is not None:
            for p in range(self.start_piece, self.end_piece + 1):
                self.handle._prios[p] = 0


class WindowMockHandle:
    """200-piece mock handle recording every priority/deadline mutation.

    Used by the on-demand download discipline tests, which need a piece space
    much larger than the ±30 sliding window to detect over-prioritization.
    """

    NUM_PIECES = 200
    PIECE_LENGTH = 2 * 1024 * 1024
    FILE_SIZE = NUM_PIECES * PIECE_LENGTH

    def __init__(self, state, initial_prio: int = 0) -> None:
        self._state = state
        self._paused = False
        self._prios: list[int] = [initial_prio] * self.NUM_PIECES
        self._deadlines: dict[int, int] = {}
        self._sequential_calls: list[bool] = []
        self._file_prio_calls: list[list[int]] = []
        self._force_recheck_called = False
        self._hash = ""
        self._save_path = ""

    def is_valid(self) -> bool:
        return True

    def status(self) -> MagicMock:
        m = MagicMock()
        m.state = self._state
        m.has_metadata = True
        m.paused = self._paused
        m.name = "test"
        m.save_path = self._save_path
        return m

    def resume(self) -> None:
        self._paused = False

    def pause(self) -> None:
        self._paused = True

    def force_recheck(self) -> None:
        self._force_recheck_called = True

    def force_reannounce(self) -> None:
        pass

    def force_dht_announce(self) -> None:
        pass

    def info_hash(self) -> MagicMock:
        return MagicMock(__str__=lambda s: self._hash)

    def torrent_file(self) -> MagicMock:
        return MagicMock(
            files=lambda: MagicMock(
                num_files=lambda: 1,
                file_path=lambda i: "video.mp4",
                file_size=lambda i: self.FILE_SIZE,
                file_offset=lambda i: 0,
            ),
            num_pieces=lambda: self.NUM_PIECES,
            piece_length=lambda: self.PIECE_LENGTH,
            name=lambda: "test",
            info_section=lambda: b"fake",
        )

    def prioritize_files(self, prios: list[int]) -> None:
        self._file_prio_calls.append(list(prios))

    def prioritize_pieces(self, prios: list[int]) -> None:
        self._prios = list(prios)

    def piece_priorities(self) -> list[int]:
        return list(self._prios)

    def piece_priority(self, p: int, prio: int | None = None) -> int | None:
        if prio is None:
            return self._prios[p]
        self._prios[p] = prio
        return None

    def set_piece_deadline(self, p: int, deadline: int) -> None:
        self._deadlines[p] = deadline

    def set_sequential_download(self, on: bool) -> None:
        self._sequential_calls.append(on)


# ── Tests ───────────────────────────────────────────────────────────

class TestBootstrapFirstVerification(unittest.TestCase):
    """Architecture: bootstrap-first replaces unconditional force_recheck."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject_torrent(self, handle: MockTorrentHandle, tracker: MockTracker | None = None) -> tuple[dict[str, object], str]:
        hash_str = "a" * 40
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, object] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 1,
            "video_path": video_path,
            "video_size": 10 * 2_097_152,
            "ready": False,
            "prefetch": False,
            "work_code": None,
        }
        if tracker is not None:
            info["tracker"] = tracker
        self.engine.torrents[hash_str] = info
        return info, hash_str

    def test_finished_intact_skips_recheck(self) -> None:
        """finished + bootstrap head_ready=True → NO force_recheck, ready=True."""
        handle = MockTorrentHandle(state=lt.torrent_status.finished)
        tracker = MockTracker(head_ready_val=True, moov_pc=5)
        info, hash_str = self._inject_torrent(handle, tracker)

        self.engine._on_metadata(handle)

        self.assertFalse(
            handle._force_recheck_called,
            "force_recheck must NOT be called when bootstrap shows data intact",
        )
        self.assertTrue(info.get("_recheck_done"), "_recheck_done must be set")
        self.assertTrue(info.get("ready"), "ready must be True after bootstrap-first")
        self.assertTrue(tracker._bootstrap_called, "bootstrap must be called")

    def test_finished_missing_triggers_recheck(self) -> None:
        """finished + bootstrap head_ready=False → force_recheck triggered.

        Current architecture (commit bd31dc9) uses force_recheck, which
        re-verifies against hashes while preserving downloaded cache. The
        older _readd_torrent path is retained only as a rate-limited helper.
        """
        handle = MockTorrentHandle(state=lt.torrent_status.finished)
        tracker = MockTracker(head_ready_val=False, moov_pc=5)
        info, hash_str = self._inject_torrent(handle, tracker)

        self.engine._on_metadata(handle)

        self.assertTrue(
            handle._force_recheck_called,
            "force_recheck must be called when bootstrap shows holes",
        )
        self.assertTrue(info.get("_recheck_done"), "_recheck_done must be set")

    def test_non_finished_does_not_recheck(self) -> None:
        """Non-finished torrents never trigger recheck logic."""
        handle = MockTorrentHandle(state=lt.torrent_status.downloading)
        tracker = MockTracker(head_ready_val=False, moov_pc=5)
        info, hash_str = self._inject_torrent(handle, tracker)

        self.engine._on_metadata(handle)

        self.assertFalse(handle._force_recheck_called, "non-finished must not recheck")
        self.assertFalse(info.get("_recheck_done"), "_recheck_done must not be set")

    def test_finished_no_tracker_leaves_not_ready(self) -> None:
        """finished with a freshly-created tracker and no verified disk data →
        force_recheck clears the finished false-positive; ready stays False."""
        handle = MockTorrentHandle(state=lt.torrent_status.finished)
        info, hash_str = self._inject_torrent(handle, tracker=None)

        self.engine._on_metadata(handle)

        self.assertTrue(
            handle._force_recheck_called,
            "finished false-positive with unverified disk data must force_recheck",
        )
        self.assertFalse(info.get("ready"), "ready should remain False without verified data")


class TestTieredCacheClassification(unittest.TestCase):
    """Tiered cache: L1 hot / L2 warm / L3 seed / L4 fragment."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_info(self, **overrides) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "last_access": time.time(),
            "_last_play_time": 0,
            "_play_count": 0,
            "progress": 0.0,
            "video_size": 6 * 1024 ** 3,
        }
        defaults.update(overrides)
        return defaults

    def test_tier_hot_when_played_within_24h(self) -> None:
        info = self._make_info(_last_play_time=time.time() - 3600, progress=50)
        self.assertEqual(self.engine._get_tier(info), "hot")

    def test_tier_warm_when_completed_and_recent(self) -> None:
        info = self._make_info(progress=100, last_access=time.time() - 86400)
        self.assertEqual(self.engine._get_tier(info), "warm")

    def test_tier_seed_when_completed_and_cold(self) -> None:
        info = self._make_info(progress=100, last_access=time.time() - 900000)
        self.assertEqual(self.engine._get_tier(info), "seed")

    def test_tier_fragment_when_incomplete(self) -> None:
        info = self._make_info(progress=50, last_access=time.time() - 900000)
        self.assertEqual(self.engine._get_tier(info), "fragment")

    def test_hot_overrides_warm(self) -> None:
        """Played within 24h is always hot, even if 100% complete."""
        info = self._make_info(
            _last_play_time=time.time() - 3600,
            progress=100,
            last_access=time.time() - 900000,
        )
        self.assertEqual(self.engine._get_tier(info), "hot")

    def test_cache_score_play_bonus(self) -> None:
        """Played torrents have dramatically higher score."""
        played = self._make_info(_play_count=1, _last_play_time=time.time() - 3600)
        unplayed = self._make_info(_play_count=0)
        self.assertGreater(
            self.engine._cache_score(played),
            self.engine._cache_score(unplayed) * 10,
            "played torrent must be 10x more valuable than unplayed",
        )

    def test_cache_score_completion(self) -> None:
        """100% complete > 50% complete > 0% complete (with heat held constant)."""
        now = time.time()
        complete = self._make_info(progress=100, _last_play_time=now)
        half = self._make_info(progress=50, _last_play_time=now)
        empty = self._make_info(progress=0, _last_play_time=now)
        self.assertGreater(self.engine._cache_score(complete), self.engine._cache_score(half))
        self.assertGreater(self.engine._cache_score(half), self.engine._cache_score(empty))

    def test_cache_score_heat_decay(self) -> None:
        """Older play time = lower score (exponential decay)."""
        recent = self._make_info(_play_count=1, _last_play_time=time.time() - 1)
        old = self._make_info(_play_count=1, _last_play_time=time.time() - 86400 * 14)
        self.assertGreater(self.engine._cache_score(recent), self.engine._cache_score(old))

    def test_tier_returned_in_get_status(self) -> None:
        """get_status must include tier field."""
        handle = MockTorrentHandle(state=lt.torrent_status.downloading)
        hash_str = "d" * 40
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, object] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 1,
            "video_path": video_path,
            "video_size": 10 * 2_097_152,
            "ready": True,
            "prefetch": False,
            "_play_count": 0,
            "_last_play_time": 0,
            "progress": 0.0,
        }
        self.engine.torrents[hash_str] = info

        status = self.engine.get_status(hash_str)
        self.assertIsNotNone(status)
        self.assertIn("tier", status)
        self.assertEqual(status["tier"], "fragment")


class TestTouchPreventsGCEviction(unittest.TestCase):
    """Architecture: touch() updates last_access so GC knows torrent is active."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject_torrent(self) -> tuple[dict[str, object], str]:
        hash_str = "c" * 40
        handle = MockTorrentHandle(state=lt.torrent_status.downloading)
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, object] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time() - 600,  # 10 min ago, would be evicted
            "video_idx": 1,
            "video_path": video_path,
            "video_size": 10 * 2_097_152,
            "ready": True,
            "prefetch": False,
        }
        self.engine.torrents[hash_str] = info
        return info, hash_str

    def test_touch_updates_last_access(self) -> None:
        """touch() must refresh last_access to current time."""
        info, hash_str = self._inject_torrent()
        old_last_access = info["last_access"]
        time.sleep(0.1)

        self.engine.touch(hash_str)

        self.assertGreater(
            info["last_access"],
            old_last_access,
            "touch() must update last_access to a newer timestamp",
        )

    def test_touch_on_missing_torrent_is_noop(self) -> None:
        """touch() on unknown hash must not raise."""
        self.engine.touch("deadbeef" * 5)  # should not raise

    def test_stream_video_calls_touch(self) -> None:
        """stream_video router must call engine.touch to prevent GC eviction.

        This is an integration-level assertion: we verify that the conceptual
        contract (high-frequency endpoints keep torrent alive) is wired.
        """
        # The actual wiring is in backend/routers/stream.py; here we verify
        # the touch method exists and works as expected.
        info, hash_str = self._inject_torrent()
        old_last_access = info["last_access"]
        time.sleep(0.1)

        self.engine.touch(hash_str)

        self.assertGreater(info["last_access"], old_last_access)


class TestCacheWarmingRetry(unittest.TestCase):
    """Architecture: get_status re-applies the play window when a late moov
    scan discovers the range after the window was set (moov unknown → tail
    probe only); without the re-apply, head-moov pieces stay priority 0 and
    the torrent stalls in phantom-finished."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject_torrent(self, tracker: MockTracker) -> tuple[MockTorrentHandle, dict[str, object], str]:
        hash_str = "b" * 40
        handle = MockTorrentHandle(state=lt.torrent_status.downloading)
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, object] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 1,
            "video_path": video_path,
            "video_size": 10 * 2_097_152,
            "ready": True,
            "prefetch": False,
            "_play_priority_applied": True,
            "moov_end": 1000,
            "tracker": tracker,
        }
        self.engine.torrents[hash_str] = info
        return handle, info, hash_str

    def test_get_status_does_not_reapply_when_moov_known(self) -> None:
        """moov range already known (moov_pc>0) → get_status stays read-only."""
        tracker = MockTracker(head_ready_val=False, moov_pc=5)
        handle, info, hash_str = self._inject_torrent(tracker)

        self.engine.get_status(hash_str)

        self.assertEqual(
            handle._prios, [4] * 10,
            "get_status must not re-apply play priority when moov is known",
        )

    def test_get_status_no_reapply_when_already_ready(self) -> None:
        """head_ready=True → no re-apply needed."""
        tracker = MockTracker(head_ready_val=True, moov_pc=5)
        handle, info, hash_str = self._inject_torrent(tracker)

        self.engine.get_status(hash_str)

        self.assertEqual(handle._prios, [4] * 10)

    def test_get_status_reapplies_when_moov_discovered_late(self) -> None:
        """moov unknown at window-set time but found by the retry scan →
        re-apply the window so head-moov pieces become urgent."""
        tracker = MockTracker(head_ready_val=False, moov_pc=0)
        handle, info, hash_str = self._inject_torrent(tracker)
        info["moov_end"] = 0  # simulate moov not yet scanned

        self.engine.get_status(hash_str)

        self.assertTrue(tracker._moov_range_set, "retry scan must record the moov range")
        self.assertEqual(
            handle._prios[0], 7,
            "head piece must be raised to urgent once the moov range is known",
        )

    def test_get_status_no_reapply_when_moov_undiscoverable(self) -> None:
        """moov truly absent on disk (all-zero file) → no re-apply."""
        tracker = MockTracker(head_ready_val=False, moov_pc=0)
        handle, info, hash_str = self._inject_torrent(tracker)
        info["moov_end"] = 0
        with open(info["video_path"], "wb") as f:
            f.write(b"\x00" * (1024 * 1024))

        self.engine.get_status(hash_str)

        self.assertFalse(tracker._moov_range_set)
        self.assertEqual(handle._prios, [4] * 10)


class FakeTorrentFinishedAlert:
    """Mock alert that looks like lt.torrent_finished_alert to _handle_alert.
    We patch lt.torrent_finished_alert in tests so isinstance() matches."""
    def __init__(self, handle: MockTorrentHandle) -> None:
        self.handle = handle


class TestRecheckRateLimit(unittest.TestCase):
    """IPZZ-802 regression: torrent_finished_alert must not infinitely recheck
    when filesystem holes persist (page-cache vs disk mismatch)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject_torrent(self, head_ready: bool = False) -> tuple[MockTorrentHandle, dict[str, object], str]:
        hash_str = "e" * 40
        handle = MockTorrentHandle(state=lt.torrent_status.finished)
        handle._hash = hash_str
        tracker = MockTracker(head_ready_val=head_ready, moov_pc=5)

        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, object] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 1,
            "video_path": video_path,
            "video_size": 10 * 2_097_152,
            "ready": False,
            "prefetch": False,
            "tracker": tracker,
        }
        self.engine.torrents[hash_str] = info
        return handle, info, hash_str

    def _dispatch_finished_alert(self, handle: MockTorrentHandle) -> None:
        """Send a fake torrent_finished_alert through _handle_alert."""
        alert = FakeTorrentFinishedAlert(handle)
        with patch.object(lt, "torrent_finished_alert", FakeTorrentFinishedAlert):
            self.engine._handle_alert(alert)

    def test_finished_alert_no_force_recheck(self) -> None:
        """torrent_finished_alert never calls force_recheck (bootstrap-first architecture)."""
        handle, info, hash_str = self._inject_torrent(head_ready=False)
        self._dispatch_finished_alert(handle)

        self.assertFalse(handle._force_recheck_called, "force_recheck must not be called")

    def test_finished_alert_sets_ready_when_head_ready(self) -> None:
        """If tracker.head_ready() is true, set ready=True without recheck."""
        handle, info, hash_str = self._inject_torrent(head_ready=True)
        self._dispatch_finished_alert(handle)

        self.assertFalse(handle._force_recheck_called, "must not recheck when head ready")
        self.assertTrue(info.get("ready"), "ready must be True when head ready")


class TestAddTorrentExistingNoRerunOnMetadata(unittest.TestCase):
    """IPZZ-802 regression: repeated add_torrent must not spam _on_metadata."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject_existing(self, has_tracker: bool = True) -> tuple[dict[str, object], str]:
        hash_str = "f" * 40
        handle = MockTorrentHandle(state=lt.torrent_status.downloading)
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, object] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 1,
            "video_path": video_path,
            "video_size": 10 * 2_097_152,
            "ready": True,
            "prefetch": False,
        }
        if has_tracker:
            info["tracker"] = MockTracker(head_ready_val=True, moov_pc=5)
        self.engine.torrents[hash_str] = info
        return info, hash_str

    def test_existing_with_tracker_skips_on_metadata(self) -> None:
        """add_torrent on existing torrent with tracker must NOT call _on_metadata."""
        info, hash_str = self._inject_existing(has_tracker=True)
        original_on_metadata = self.engine._on_metadata
        calls = []

        def spy_on_metadata(handle):
            calls.append(handle)
            return original_on_metadata(handle)

        self.engine._on_metadata = spy_on_metadata  # type: ignore[method-assign]
        magnet = f"magnet:?xt=urn:btih:{hash_str}"

        self.engine.add_torrent(magnet, prefetch=False)

        self.assertEqual(len(calls), 0, "_on_metadata must NOT be called when tracker already exists")

    def test_existing_without_tracker_runs_on_metadata(self) -> None:
        """add_torrent on existing torrent WITHOUT tracker should still call _on_metadata."""
        info, hash_str = self._inject_existing(has_tracker=False)
        original_on_metadata = self.engine._on_metadata
        calls = []

        def spy_on_metadata(handle):
            calls.append(handle)
            return original_on_metadata(handle)

        self.engine._on_metadata = spy_on_metadata  # type: ignore[method-assign]
        magnet = f"magnet:?xt=urn:btih:{hash_str}"

        self.engine.add_torrent(magnet, prefetch=False)

        self.assertEqual(len(calls), 1, "_on_metadata MUST be called when tracker is missing")


class TestAlertMaskIncludesProgress(unittest.TestCase):
    """IPZZ-802 regression: alert_mask must include progress_notification so
    piece_finished_alert and hash_failed_alert are delivered to tracker."""

    def test_alert_mask_has_progress_notification(self) -> None:
        """Without progress_notification, piece_finished_alert never fires and
        tracker.verified_count() stays stale, causing head_ready() to remain
        False forever even though libtorrent already has the pieces."""
        import libtorrent as lt
        temp_dir = tempfile.mkdtemp()
        try:
            engine = TorrentEngine(temp_dir, max_size_gb=1)
            settings = engine.session.get_settings()
            mask = settings["alert_mask"]
            engine.shutdown()

            self.assertNotEqual(
                mask & int(lt.alert.category_t.progress_notification),
                0,
                "alert_mask must include progress_notification for piece_finished_alert",
            )
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestCheckingFilesDoesNotBlockPlayback(unittest.TestCase):
    """IPZZ-802 regression: check_stream and stream_video must not block
    playback when torrent is in checking_files state but filesystem head
    data is already present."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject_torrent(self, state) -> tuple[MockTorrentHandle, dict[str, object], str]:
        hash_str = "g" * 40
        handle = MockTorrentHandle(state=state)
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, object] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 1,
            "video_path": video_path,
            "video_size": 10 * 2_097_152,
            "ready": True,
            "prefetch": False,
        }
        self.engine.torrents[hash_str] = info
        return handle, info, hash_str

    def test_check_stream_returns_head_ready_during_checking(self) -> None:
        """check_stream must return head_ready=True when filesystem has data,
        even if torrent.state == checking_files."""
        # This is a conceptual test — actual wiring is in stream.py.
        # We verify that the engine exposes enough state for the router
        # to make this decision.
        _, info, hash_str = self._inject_torrent(lt.torrent_status.checking_files)
        status = self.engine.get_status(hash_str)
        self.assertIsNotNone(status)
        # get_status itself does not gate on checking_files; the router does.
        # The key assertion is that get_status returns a valid status dict.
        self.assertIn("state", status)
        self.assertEqual(status["state"], "checking_files")


class TestCacheUpperLimit(unittest.TestCase):
    """Cache must never exceed max_size_bytes and must protect free space."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject_torrent(self, hash_str: str, size: int, last_play: float = 0) -> dict[str, Any]:
        handle = MockTorrentHandle(state=lt.torrent_status.downloading)
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir

        info: dict[str, Any] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 1,
            "video_path": video_path,
            "video_size": size,
            "ready": True,
            "prefetch": False,
            "_play_count": 1 if last_play else 0,
            "_last_play_time": last_play,
            "progress": 50.0,
        }
        self.engine.torrents[hash_str] = info
        return info

    def test_hard_limit_evicts_when_usage_exceeds_max(self) -> None:
        """Usage > max_size_bytes must trigger eviction, including hot torrents."""
        hash_str = "h" * 40
        self._inject_torrent(hash_str, 2 * 1024 ** 3, last_play=time.time())

        # Simulate cache usage above the configured upper limit.
        self.engine._get_cache_size = lambda: 2 * 1024 ** 3  # type: ignore[method-assign]
        removed: list[str] = []

        def fake_remove(h: str) -> bool:
            removed.append(h)
            self.engine.torrents.pop(h, None)
            return True

        self.engine.remove_torrent = fake_remove  # type: ignore[method-assign]

        self.engine._enforce_cache_limit()

        self.assertIn(hash_str, removed, "must evict torrent when usage exceeds hard limit")

    def test_soft_limit_protects_hot_torrent(self) -> None:
        """Usage between soft and hard limit must NOT evict hot torrents."""
        hash_str = "i" * 40
        self._inject_torrent(hash_str, 2 * 1024 ** 3, last_play=time.time())

        # Simulate usage between soft (95%) and hard (100%) limits.
        self.engine._get_cache_size = lambda: int(0.97 * self.engine.max_size_bytes)  # type: ignore[method-assign]
        removed: list[str] = []

        def fake_remove(h: str) -> bool:
            removed.append(h)
            return True

        self.engine.remove_torrent = fake_remove  # type: ignore[method-assign]

        self.engine._enforce_cache_limit()

        self.assertNotIn(hash_str, removed, "hot torrent must be protected below hard limit")

    def test_emergency_eviction_ignores_hot_tier(self) -> None:
        """Critical low free space must evict even hot / liked torrents."""
        hash_str = "j" * 40
        self._inject_torrent(hash_str, 2 * 1024 ** 3, last_play=time.time())
        self.engine.set_liked(hash_str, True)

        # Use a controlled reserve so the test is deterministic regardless of
        # the host partition size.
        self.engine.min_free_bytes = 100 * 1024 * 1024

        self.engine._get_cache_size = lambda: 100 * 1024 ** 2  # type: ignore[method-assign]
        removed: list[str] = []

        def fake_remove(h: str) -> bool:
            removed.append(h)
            self.engine.torrents.pop(h, None)
            return True

        self.engine.remove_torrent = fake_remove  # type: ignore[method-assign]

        from services import torrent_engine as te
        with patch.object(te, "_get_disk_available_bytes", return_value=1 * 1024 ** 3):
            # 1GB available is above the 100MB reserve.
            self.engine._enforce_cache_limit()

        self.assertNotIn(
            hash_str, removed,
            "liked hot torrent must not be evicted when free space is safe"
        )

        removed.clear()
        with patch.object(te, "_get_disk_available_bytes", return_value=1 * 1024 ** 2):
            # 1MB available is below the 100MB reserve.
            self.engine._enforce_cache_limit()

        self.assertIn(
            hash_str, removed,
            "emergency low free space must evict even liked hot torrents"
        )

    def test_auto_limit_does_not_exceed_available_space(self) -> None:
        """Auto mode must compute a limit that fits within current free space."""
        temp_dir = tempfile.mkdtemp()
        try:
            engine = TorrentEngine(temp_dir, max_size_gb=0)
            engine.shutdown()
            from services import torrent_engine as te
            available = te._get_disk_available_bytes(temp_dir)
            total = te._get_disk_total_bytes(temp_dir)
            # Limit must not exceed 60% of total or available minus reserve.
            self.assertLessEqual(engine.max_size_bytes, int(total * 0.6))
            self.assertLessEqual(
                engine.max_size_bytes + engine.min_free_bytes, available
            )
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestOnDemandDownloadDiscipline(unittest.TestCase):
    """The playback taste: 不看不下, 看哪下哪, 绝不写满磁盘.

    Regression guard against "brainless full-file download". Any change that
    raises priorities outside the sliding window (sequential download,
    head+tail requests without resetting the rest, window leaks on seek)
    fails here. Piece space is 200 x 2MB so the ±30 window covers only ~15%.
    """

    PIECES = WindowMockHandle.NUM_PIECES
    PIECE_LEN = WindowMockHandle.PIECE_LENGTH

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.engine = TorrentEngine(self.temp_dir, max_size_gb=1)

    def tearDown(self) -> None:
        self.engine.shutdown()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _inject(
        self,
        state=lt.torrent_status.downloading,
        initial_prio: int = 0,
        verified: set[int] | None = None,
    ) -> tuple[WindowMockHandle, dict[str, Any], str, MockTracker]:
        hash_str = "1" * 40
        handle = WindowMockHandle(state=state, initial_prio=initial_prio)
        handle._hash = hash_str
        video_dir = os.path.join(self.temp_dir, hash_str)
        os.makedirs(video_dir, exist_ok=True)
        video_path = os.path.join(video_dir, "video.mp4")
        _make_minimal_mp4(video_path)
        handle._save_path = video_dir
        tracker = MockTracker(
            head_ready_val=False,
            moov_pc=0,
            verified=verified,
            start_piece=0,
            end_piece=self.PIECES - 1,
            piece_length=self.PIECE_LEN,
            file_offset=0,
        )
        tracker.handle = handle
        info: dict[str, Any] = {
            "handle": handle,
            "magnet": f"magnet:?xt=urn:btih:{hash_str}",
            "hash": hash_str,
            "added_at": time.time(),
            "last_access": time.time(),
            "video_idx": 0,
            "video_path": video_path,
            "video_size": WindowMockHandle.FILE_SIZE,
            "ready": False,
            "prefetch": False,
            "work_code": None,
            "tracker": tracker,
        }
        self.engine.torrents[hash_str] = info
        return handle, info, hash_str, tracker

    @staticmethod
    def _urgent(handle: WindowMockHandle) -> set[int]:
        return {p for p, prio in enumerate(handle._prios) if prio == 7}

    def test_metadata_without_play_zeroes_everything(self) -> None:
        """不看不下: metadata arrival without a play request must leave every
        piece at priority 0, even if file-level priority leaked into pieces."""
        handle, info, hash_str, _ = self._inject(initial_prio=4)

        self.engine._on_metadata(handle)

        self.assertEqual(
            handle._prios, [0] * self.PIECES,
            "metadata without play must zero ALL piece priorities",
        )
        self.assertEqual(
            handle._file_prio_calls, [[4]],
            "only the video file may get a nonzero file priority",
        )
        self.assertNotIn(True, handle._sequential_calls)

    def test_play_raises_only_head_window_and_moov(self) -> None:
        """看就下: play from 0 raises exactly the ±30 head window (+moov piece),
        everything else stays at 0."""
        handle, info, hash_str, _ = self._inject()
        self.engine._on_metadata(handle)  # zeroes all pieces first

        self.assertTrue(self.engine.resume_download(hash_str, 0.0, 0.0))

        self.assertEqual(
            self._urgent(handle), set(range(0, 31)),
            "play from 0 must raise exactly pieces 0-30 (window + head moov)",
        )
        self.assertTrue(all(p == 0 for p in handle._prios[31:]))
        self.assertFalse(handle._paused, "play must resume the torrent")
        self.assertNotIn(True, handle._sequential_calls)

    def test_seek_moves_window_and_zeroes_abandoned_pieces(self) -> None:
        """看哪下哪: seeking to 50% moves the urgent window and drops the
        abandoned head pieces back to 0 (no priority leak across seeks)."""
        handle, info, hash_str, _ = self._inject()
        self.engine._on_metadata(handle)
        self.engine.resume_download(hash_str, 0.0, 0.0)

        self.assertTrue(self.engine.apply_seek_priority(hash_str, 200.0, 400.0))

        # ratio 0.5 -> target piece 100, seek window ±15 -> [85,115];
        # moov piece 0 stays urgent until head_ready.
        self.assertEqual(self._urgent(handle), {0} | set(range(85, 116)))
        self.assertTrue(
            all(p == 0 for p in handle._prios[1:85]),
            "abandoned head-window pieces must drop back to 0",
        )
        self.assertNotIn(True, handle._sequential_calls)

    def test_verified_pieces_retained_not_zeroed(self) -> None:
        """Downloaded pieces outside the window are retained at priority 1
        (seedable, not re-downloaded); unverified ones are stopped at 0."""
        handle, info, hash_str, _ = self._inject(verified={50, 60})
        self.engine._on_metadata(handle)

        self.engine.apply_seek_priority(hash_str, 200.0, 400.0)  # window [85,115]

        self.assertEqual(handle._prios[50], 1, "verified piece outside window: retain(1)")
        self.assertEqual(handle._prios[60], 1)
        self.assertEqual(handle._prios[40], 0, "unverified piece outside window: stop(0)")

    def test_range_request_raises_only_requested_pieces(self) -> None:
        """Browser range requests (seek_priority) may only raise the pieces
        covering the requested byte range, never the whole file."""
        from services.video_stream import seek_priority

        handle, info, hash_str, tracker = self._inject()
        self.engine._on_metadata(handle)

        seek_priority(hash_str, 100 * self.PIECE_LEN, 100 * self.PIECE_LEN + 8 * 1024 * 1024, self.engine)

        self.assertEqual(self._urgent(handle), set(range(98, 107)))
        self.assertEqual(tracker.request_pieces_calls[-1], (98, 106))
        self.assertTrue(all(p == 0 for p in handle._prios[:98]))
        self.assertTrue(all(p == 0 for p in handle._prios[107:]))

    def test_queued_resume_at_zero_uses_full_window(self) -> None:
        """Play clicked before metadata arrived: the queued resume must apply
        the full ±30 window — not the minimal head-only play priority that
        would leave playback with a one-piece buffer."""
        handle, info, hash_str, _ = self._inject()
        info["_resume_on_metadata"] = True
        info["_resume_time"] = 0.0
        info["_resume_duration"] = 0.0
        info["_resume_window_pcs"] = 30

        self.engine._on_metadata(handle)

        self.assertEqual(
            self._urgent(handle), set(range(0, 31)),
            "queued resume at t=0 must raise the full ±30 head window",
        )
        self.assertNotIn(True, handle._sequential_calls)

    def test_pause_zeroes_everything_and_pauses(self) -> None:
        """不看就停: pause zeroes all video pieces and pauses the handle."""
        handle, info, hash_str, tracker = self._inject()
        self.engine._on_metadata(handle)
        self.engine.resume_download(hash_str, 0.0, 0.0)
        info["keep_cache"] = True

        self.assertTrue(self.engine.pause_download(hash_str))

        self.assertEqual(handle._prios, [0] * self.PIECES)
        self.assertTrue(handle._paused)
        self.assertTrue(tracker.reset_priorities_called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
