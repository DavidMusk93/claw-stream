#!/usr/bin/env python3
"""On-demand download discipline — end-to-end regression with a real local seed.

The playback taste: 不看不下, 看哪下哪, 绝不写满磁盘.
- playing from 0 downloads ONLY the head window + moov probe, never the whole file
- seeking to the tail moves the window; the unwatched middle stays a hole on disk

Runs against the local BT fixture (tests/fixtures/test_video.mp4, 138 x 16KB
pieces). The ±30-piece window (~500KB) vs the full 2.25MB file makes any
over-prioritization detectable within seconds on localhost.

Note on wait loops: the fixture hash has no tracker, so the waits emulate real
player traffic (seek_priority + progress reports + connect_peer retries).
This is what production playback does via /stream and /torrent/progress. The
one readd escape hatch mirrors read_video_range's hole-timeout recovery and
emulates the fresh peer supply a production swarm gets from tracker/DHT — the
fixture's single seed carries reconnect backoff that no libtorrent API resets.

Run: uv run python -m pytest tests/test_ondemand_download.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest

import libtorrent as lt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.torrent_engine import TorrentEngine, _range_has_data
from services.video_stream import seek_priority
from tests.local_bt_fixture import LocalSeed

PIECE_LEN = 16384
NUM_PIECES = 138
FILE_SIZE = 2257466
DURATION = 60.0


def _piece_has_data(path: str, piece: int) -> bool:
    """Ground truth from the filesystem: is the whole piece range allocated?"""
    return _range_has_data(path, piece * PIECE_LEN, (piece + 1) * PIECE_LEN - 1)


class TestOnDemandDownloadE2E(unittest.TestCase):
    """Full-stack guard: engine + real libtorrent session + local seed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seed = LocalSeed()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.seed.stop()

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="star_bt_ondemand_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _player_wait(self, engine, hash_str: str, cond, playhead_t: float = 0.0, timeout: float = 30.0) -> bool:
        """Wait for cond while emulating real player traffic at playhead_t.

        Every tick: refresh the seed peer (fixture has no tracker), nudge the
        requested range (what /stream does), and re-apply the sliding window
        (what /torrent/progress does). Handles are re-fetched each tick because
        the phantom-finished recovery path (_readd_torrent) swaps them.
        """
        playhead_byte = int(FILE_SIZE * playhead_t / DURATION)
        deadline = time.time() + timeout
        readded = False
        while time.time() < deadline:
            if cond():
                return True
            with engine.lock:
                info = engine.torrents.get(hash_str)
            if info:
                info["handle"].connect_peer(("127.0.0.1", self.seed.listen_port), 0)
            # A real range request is ~4 pieces wide here — an 8MB chunk would
            # span the whole fixture (16KB pieces) and defeat the test.
            seek_priority(hash_str, playhead_byte, playhead_byte + 4 * PIECE_LEN - 1, engine)
            engine.update_play_progress(hash_str, playhead_t, DURATION)
            # Production hole-timeout recovery (read_video_range): if data
            # still hasn't arrived after 10s and libtorrent sits in finished,
            # re-add the torrent once to clear its stale state + peer backoff.
            # The "0 peers while downloading" variant emulates what a real
            # swarm provides for free (fresh peer entries via tracker/DHT);
            # the fixture's single seed carries reconnect backoff that no API
            # resets, so a readd is the only localhost-equivalent recovery.
            if not readded and time.time() > deadline - timeout + 10 and info:
                try:
                    st = info["handle"].status()
                    if st.state == lt.torrent_status.finished or (
                        st.state == lt.torrent_status.downloading and st.num_peers == 0
                    ):
                        engine._readd_torrent(hash_str)
                        readded = True
                except Exception:
                    pass
            time.sleep(0.5)
        # Timeout — dump libtorrent internals to make the failure diagnosable.
        with engine.lock:
            info = engine.torrents.get(hash_str)
        if info:
            try:
                h = info["handle"]
                st = h.status()
                prios = h.piece_priorities()
                nonzero = [(i, p) for i, p in enumerate(prios) if p > 0]
                print(f"\n[player_wait timeout] state={st.state} peers={st.num_peers} "
                      f"list_peers={st.list_peers} done={st.total_done} "
                      f"nonzero_prios={nonzero}")
                for pi in h.get_peer_info():
                    print(f"  peer ip={pi.ip} pieces={pi.num_pieces}/{NUM_PIECES} "
                          f"down={pi.down_speed}B/s failcount={pi.failcount}")
            except Exception as e:
                print(f"\n[player_wait timeout] introspection failed: {e}")
        return False

    def test_play_downloads_window_only_never_whole_file(self) -> None:
        engine = TorrentEngine(self.tmp_dir, max_size_gb=20)
        hash_str = self.seed.hash
        try:
            info = engine.add_torrent(f"magnet:?xt=urn:btih:{hash_str}", prefetch=False)
            self.assertIsNotNone(info, "add_torrent failed")
            info["handle"].connect_peer(("127.0.0.1", self.seed.listen_port), 0)

            # The ONLY user action in this test: hit play at 0:00.
            self.assertTrue(engine.resume_download(hash_str, 0.0, 0.0))

            self.assertTrue(
                self._player_wait(engine, hash_str, lambda: info.get("_metadata_done") or (
                    engine.torrents.get(hash_str, {}).get("_metadata_done")
                )),
                "metadata never arrived from local seed",
            )

            def head_ready():
                with engine.lock:
                    cur = engine.torrents.get(hash_str)
                path = cur and cur.get("video_path")
                return bool(path and os.path.exists(path) and _piece_has_data(path, 0))

            # 看就尽可能快: the head window must arrive promptly.
            self.assertTrue(
                self._player_wait(engine, hash_str, head_ready),
                "head window did not download",
            )

            with engine.lock:
                video_path = engine.torrents[hash_str]["video_path"]

            # Let the engine settle. On localhost any over-prioritized piece
            # would arrive within these seconds.
            for _ in range(12):
                engine.get_status(hash_str)  # drives moov discovery, keeps status fresh
                time.sleep(0.25)

            total = os.path.getsize(video_path)
            allocated = os.stat(video_path).st_blocks * 512

            # 不看不下: the unwatched middle must stay a hole.
            self.assertFalse(
                _piece_has_data(video_path, 60),
                "middle piece 60 downloaded without being watched — window discipline broken",
            )
            # Piece 112 is inside the post-seek window but outside both the
            # initial head window and the tail moov probe — must be empty now.
            self.assertFalse(
                _piece_has_data(video_path, 112),
                "piece 112 downloaded before any seek — tail probe/window too wide",
            )
            self.assertLess(
                allocated, int(total * 0.7),
                f"downloaded {allocated}/{total} bytes — expected head window + tail probe only",
            )

            # 看哪下哪: seek to 90% (54s of 60s) — the window must move to the tail.
            self.assertTrue(engine.apply_seek_priority(hash_str, 54.0, DURATION))
            self.assertTrue(
                self._player_wait(engine, hash_str, lambda: _piece_has_data(video_path, 112), playhead_t=54.0),
                "seek window at the tail did not download",
            )

            for _ in range(8):
                engine.get_status(hash_str)
                time.sleep(0.25)

            # The middle must STILL be empty after the seek.
            self.assertFalse(
                _piece_has_data(video_path, 60),
                "middle piece 60 downloaded after seek — abandoned pieces not zeroed",
            )
            allocated = os.stat(video_path).st_blocks * 512
            self.assertLess(
                allocated, int(total * 0.8),
                f"downloaded {allocated}/{total} after seek — sliding window leaked",
            )
        finally:
            engine.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
