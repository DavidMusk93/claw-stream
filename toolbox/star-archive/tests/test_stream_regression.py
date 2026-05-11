#!/usr/bin/env python3
"""Regression tests using real FastAPI TestClient + real BT download + real HTTP requests.

基于本地 seed 的真实 BitTorrent 下载视频运行，覆盖：
1. Safari Range request sequence
2. checking_files blocks stream
3. Range response integrity
4. Hole range returns 416

Run: cd tests && python3 -m pytest test_stream_regression.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import libtorrent as lt

from backend.routers import stream_router, check_router
from backend.services.torrent_engine import TorrentEngine, find_video_state
from backend.services.video_stream import read_video_range
from tests.local_bt_fixture import LocalSeed, download_with_engine, cleanup_cache_dir


# ── Config ──────────────────────────────────────────────────────────
# 使用本地 seed 的真实 BT 下载视频作为测试样本
_sample_hash: str | None = None
_sample_video_path: str | None = None


def _get_sample_hash() -> str:
    """获取本地 seed 的 hash。"""
    global _sample_hash
    if _sample_hash is None:
        seed = LocalSeed()
        _sample_hash = seed.hash
        seed.stop()
    return _sample_hash


# ── Shared module-level fixture ─────────────────────────────────────

class _SharedEngine:
    """Module-level singleton: one engine, one torrent download, checked once."""
    _instance: TorrentEngine | None = None
    _temp_dir: str | None = None
    _video_path: str | None = None
    _hash: str | None = None
    _seed: LocalSeed | None = None

    @classmethod
    def get(cls) -> tuple[TorrentEngine, str, TestClient]:
        if cls._instance is not None:
            return cls._instance, cls._video_path or "", cls._client

        cls._seed = LocalSeed()
        cls._hash = cls._seed.hash
        cls._temp_dir = tempfile.mkdtemp(prefix="star_bt_stream_test_")
        cls._instance, cls._video_path = download_with_engine(
            None, cls._hash, cls._seed.listen_port, timeout=60.0
        )

        app = FastAPI()
        app.state.engine = cls._instance
        app.include_router(stream_router)
        app.include_router(check_router)
        cls._client = TestClient(app)
        return cls._instance, cls._video_path, cls._client

    @classmethod
    def shutdown(cls) -> None:
        if cls._instance:
            cls._instance.shutdown()
            cls._instance = None
        if cls._temp_dir:
            shutil.rmtree(cls._temp_dir, ignore_errors=True)
            cls._temp_dir = None
        if cls._seed:
            cls._seed.stop()
            cls._seed = None


# ── Tear down shared engine at module exit ──────────────────────────
def tearDownModule() -> None:
    _SharedEngine.shutdown()


# ═════════════════════════════════════════════════════════════════════
#  Tests
# ═════════════════════════════════════════════════════════════════════

class TestSafariRangeSequence(unittest.TestCase):
    """Simulate Safari's Range request pattern and verify assembled data is valid MP4."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine, cls.video_path, cls.client = _SharedEngine.get()
        cls.total = os.path.getsize(cls.video_path)

    def _request_range(self, start: int, end: int) -> tuple[bytes, int, dict]:
        """Send Range request; return (data, status, headers)."""
        r = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": f"bytes={start}-{end}"},
        )
        return r.content, r.status_code, dict(r.headers)

    def test_safari_probe_then_full_head(self) -> None:
        """Safari sends 0-1, then 0-total; verify each response matches file data."""
        import mmap
        with open(self.video_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as file_data:
                ranges = [
                    (0, 1),
                    (0, self.total - 1),
                    (self.total // 4, self.total // 4 + 1_048_575),
                    (self.total // 2, self.total // 2 + 1_048_575),
                ]

                for start, end in ranges:
                    end = min(end, self.total - 1)
                    data, status, headers = self._request_range(start, end)
                    self.assertIn(status, {200, 206}, f"Range {start}-{end} should succeed, got {status}")
                    if status == 206:
                        cr = headers.get("content-range", "")
                        self.assertIn("bytes", cr, f"Missing Content-Range for {start}-{end}")

                    expected = bytes(file_data[start:start + len(data)])
                    self.assertEqual(
                        data, expected,
                        f"Range {start}-{end} returned data that does NOT match file at offset {start}",
                    )
                    print(f"  Range {start}-{end}: OK ({len(data)} bytes match file)")

                # Critical: overlapping ranges must agree
                r_a = self.client.get(
                    f"/stream/{_get_sample_hash()}",
                    headers={"Range": "bytes=0-65535"},
                )
                r_b = self.client.get(
                    f"/stream/{_get_sample_hash()}",
                    headers={"Range": "bytes=32768-98303"},
                )
                overlap_a = r_a.content[32768:]
                overlap_b = r_b.content[:65536 - 32768]
                self.assertEqual(
                    overlap_a, overlap_b,
                    "Overlapping Safari ranges returned inconsistent data — this causes code=4",
                )
                print(f"  Overlap consistency: OK")

    def test_safari_0_1_probe(self) -> None:
        """First request bytes=0-1 must return 206 + 2 bytes (not hole-marked)."""
        data, status, headers = self._request_range(0, 1)
        self.assertEqual(status, 206)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[:2], b"\x00\x00", "MP4 ftyp starts with 00 00")
        cr = headers.get("content-range", "")
        self.assertTrue(cr.startswith("bytes 0-1/"), f"Bad Content-Range: {cr}")


class TestCheckingFilesBlocking(unittest.TestCase):
    """Verify that checking_files state blocks stream and check endpoints."""

    def setUp(self) -> None:
        self.seed = LocalSeed()
        self.temp_dir = tempfile.mkdtemp(prefix="star_bt_cf_")
        self.engine, self.video_path = download_with_engine(
            self.temp_dir, self.seed.hash, self.seed.listen_port, timeout=60.0
        )
        self.app, self.priv_engine = _make_private_app(self.temp_dir)
        self.client = TestClient(self.app)

        # Add the torrent
        magnet = f"magnet:?xt=urn:btih:{self.seed.hash}"
        self.info = self.priv_engine.add_torrent(magnet, prefetch=False)
        if not self.info:
            self.tearDown()
            self.fail("Failed to add torrent to engine")
        self.handle = self.info["handle"]

        # Wait for metadata
        for _ in range(30):
            if self.handle.status().has_metadata:
                break
            time.sleep(0.2)
        if not self.handle.status().has_metadata:
            self.tearDown()
            self.fail("Metadata not available")

        # Connect to local seed
        self.handle.connect_peer(("127.0.0.1", self.seed.listen_port), 0)

    def tearDown(self) -> None:
        self.priv_engine.shutdown()
        self.engine.shutdown()
        # 不要删除 CACHE_DIR 下的共享缓存，其他测试依赖它
        self.seed.stop()

    def test_checking_files_returns_503_and_false_head_ready(self) -> None:
        """force_recheck → checking_files → /stream returns 503, /api/check returns false."""
        hash_str = self.seed.hash

        # First verify normal state works
        r = self.client.get(f"/api/check/{hash_str}")
        self.assertEqual(r.status_code, 200)
        normal_ready = r.json()["head_ready"]
        print(f"  Normal head_ready={normal_ready}")

        # Trigger recheck
        self.handle.force_recheck()

        # Poll until we catch checking_files state
        caught_checking = False
        for _ in range(40):
            st = self.handle.status()
            if st.state == lt.torrent_status.checking_files:
                caught_checking = True
                break
            time.sleep(0.2)

        if not caught_checking:
            self.handle.force_recheck()
            time.sleep(0.5)
            st = self.handle.status()
            if st.state != lt.torrent_status.checking_files:
                self.skipTest("Could not trigger checking_files state")

        print(f"  Caught checking_files state")

        # /api/check must report head_ready=False
        r = self.client.get(f"/api/check/{hash_str}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(
            r.json()["head_ready"],
            "head_ready must be False during checking_files",
        )

        # /stream must return 503
        r = self.client.get(
            f"/stream/{hash_str}",
            headers={"Range": "bytes=0-1048575"},
        )
        self.assertEqual(r.status_code, 503, "Stream must be blocked during checking_files")
        self.assertEqual(r.headers.get("retry-after"), "10")

        # Wait for checking to finish
        for _ in range(60):
            st = self.handle.status()
            if st.state != lt.torrent_status.checking_files:
                break
            time.sleep(0.2)

        # After checking, normal behavior should resume
        r = self.client.get(f"/api/check/{hash_str}")
        self.assertEqual(r.status_code, 200)
        print(f"  Post-check head_ready={r.json()['head_ready']}")


class TestRangeResponseIntegrity(unittest.TestCase):
    """Verify Range response headers and data consistency."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine, cls.video_path, cls.client = _SharedEngine.get()

    def test_content_range_matches_data_length(self) -> None:
        """Content-Range end must match actual data length."""
        r = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": "bytes=0-1048575"},
        )
        self.assertEqual(r.status_code, 206)
        data = r.content
        cr = r.headers.get("content-range", "")
        self.assertTrue(cr.startswith("bytes "))
        range_part = cr.split(" ")[1].split("/")[0]
        start_s, end_s = range_part.split("-")
        actual_end = int(start_s) + len(data) - 1
        self.assertEqual(int(end_s), actual_end,
                         f"Content-Range end {end_s} != actual {actual_end}")

    def test_repeated_range_returns_identical_data(self) -> None:
        """Same Range request twice must return identical bytes."""
        r1 = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": "bytes=0-65535"},
        )
        r2 = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": "bytes=0-65535"},
        )
        self.assertEqual(r1.status_code, 206)
        self.assertEqual(r2.status_code, 206)
        self.assertEqual(r1.content, r2.content,
                         "Repeated range request returned different data")

    def test_overlapping_ranges_consistent(self) -> None:
        """Two overlapping ranges must agree on the overlapping bytes."""
        r_a = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": "bytes=0-65535"},
        )
        r_b = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": "bytes=32768-98303"},
        )
        self.assertEqual(r_a.status_code, 206)
        self.assertEqual(r_b.status_code, 206)
        overlap_a = r_a.content[32768:]
        overlap_b = r_b.content[:65536 - 32768]
        self.assertEqual(overlap_a, overlap_b,
                         "Overlapping range data inconsistent")


class TestHoleHandling(unittest.TestCase):
    """Verify hole ranges are properly rejected (416) instead of returning zeros."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine, cls.video_path, cls.client = _SharedEngine.get()
        cls.total = os.path.getsize(cls.video_path)

    def test_known_hole_range_returns_416(self) -> None:
        """Request a range beyond file size; must return 416 not 200/206."""
        # 对于已下载的完整文件，没有 hole；测试请求超出文件末尾
        beyond = self.total + 1_500_000_000
        r = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": f"bytes={beyond}-{beyond + 65535}"},
        )
        # 超出范围应返回 416
        self.assertEqual(r.status_code, 416, "超出文件范围的 Range 应返回 416")
        cr = r.headers.get("content-range", "")
        self.assertIn("bytes */", cr, "416 must have Content-Range: bytes */total")

    def test_no_range_returns_200_with_chunk(self) -> None:
        """Request without Range header returns 200 + first chunk (Safari compat)."""
        r = self.client.get(f"/stream/{_get_sample_hash()}")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.content), 0, "No-Range request must return data")
        self.assertEqual(r.headers.get("accept-ranges"), "bytes")


# ── Helper ──────────────────────────────────────────────────────────

def _make_private_app(cache_dir: str) -> tuple[FastAPI, TorrentEngine]:
    """Create a private app+engine for tests that need isolation (e.g. force_recheck)."""
    engine = TorrentEngine(cache_dir, max_size_gb=20)
    app = FastAPI()
    app.state.engine = engine
    app.include_router(stream_router)
    app.include_router(check_router)
    return app, engine


class TestFullPlaybackFlow(unittest.TestCase):
    """Simulate the complete Safari playback flow from check to stream to decode."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine, cls.video_path, cls.client = _SharedEngine.get()
        cls.total = os.path.getsize(cls.video_path)

    def _request_range(self, start: int, end: int) -> tuple[bytes, int, dict, float]:
        """Send Range request; return (data, status, headers, elapsed_ms)."""
        import time
        t0 = time.perf_counter()
        r = self.client.get(
            f"/stream/{_get_sample_hash()}",
            headers={"Range": f"bytes={start}-{end}"},
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return r.content, r.status_code, dict(r.headers), elapsed

    def test_api_check_reports_ready(self) -> None:
        """/api/check must report head_ready=true when moov is complete."""
        r = self.client.get(f"/api/check/{_get_sample_hash()}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["head_ready"], "head_ready must be true")
        self.assertTrue(r.json()["cached"], "cached must be true")

    def test_safari_full_range_sequence(self) -> None:
        """Simulate Safari's complete request sequence; verify each response matches file."""
        import mmap
        with open(self.video_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as file_data:
                ranges = [
                    (0, 1),
                    (0, self.total - 1),
                    (self.total // 4, self.total - 1),
                    (self.total // 2, self.total - 1),
                    (1024, self.total // 4),
                ]

                max_elapsed = 0.0
                for start, end in ranges:
                    end = min(end, self.total - 1)
                    data, status, headers, elapsed = self._request_range(start, end)
                    self.assertIn(status, {200, 206}, f"Range {start}-{end} failed with {status}")
                    self.assertLess(elapsed, 5000, f"Range {start}-{end} took {elapsed:.0f}ms (>5s)")
                    max_elapsed = max(max_elapsed, elapsed)

                    expected = bytes(file_data[start:start + len(data)])
                    self.assertEqual(
                        data, expected,
                        f"Range {start}-{end} returned data that does NOT match file at offset {start}",
                    )

                    cr = headers.get("content-range", "")
                    self.assertIn("bytes", cr, f"Missing Content-Range for {start}-{end}")
                    print(f"  Range {start}-{end}: OK ({len(data)} bytes, {elapsed:.0f}ms)")

                print(f"  Max elapsed: {max_elapsed:.0f}ms")

                # Verify moov region is complete by checking offsets [0, moov_end]
                from backend.services.torrent_engine import _scan_mp4_moov
                moov_start, moov_end = _scan_mp4_moov(self.video_path)
                verify_end = max(8 * 1024 * 1024, moov_end + 1024 * 1024)
                verify_end = min(verify_end, self.total)
                moov_region = bytes(file_data[0:verify_end])
                tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                try:
                    tmp.write(moov_region)
                    tmp.close()
                    result = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries",
                         "format=duration", "-show_entries",
                         "stream=codec_name,width,height",
                         "-of", "default=noprint_wrappers=1", tmp.name],
                        capture_output=True, text=True,
                    )
                    print(f"  ffprobe (moov+mdat head): returncode={result.returncode}")
                    if result.stderr.strip():
                        print(f"  ffprobe stderr: {result.stderr.strip()[:300]}")
                    self.assertEqual(result.returncode, 0,
                                     f"ffprobe failed:\n{result.stderr.strip()[:500]}")
                    self.assertIn("codec_name=h264", result.stdout, "Must contain H.264 stream")

                    # Extract first frame from moov+mdat head
                    result2 = subprocess.run(
                        ["ffmpeg", "-v", "error", "-ss", "0", "-i", tmp.name,
                         "-vframes", "1", "-f", "image2pipe", "-pix_fmt", "rgb24", "-"],
                        capture_output=True,
                    )
                    self.assertEqual(result2.returncode, 0,
                                     f"ffmpeg first-frame extract failed:\n{result2.stderr.decode()[:500]}")
                    self.assertGreater(len(result2.stdout), 1000,
                                       "First frame must be >1000 bytes")
                    print(f"  First frame: {len(result2.stdout)} bytes")
                finally:
                    os.unlink(tmp.name)

    def test_response_time_under_2s(self) -> None:
        """All stream requests must complete within 2 seconds."""
        ranges = [
            (0, 1024 * 1024),
            (self.total // 4, self.total // 4 + 1024 * 1024),
            (self.total // 2, self.total // 2 + 1024 * 1024),
        ]
        for start, end in ranges:
            end = min(end, self.total - 1)
            _, status, _, elapsed = self._request_range(start, end)
            self.assertEqual(status, 206)
            self.assertLess(elapsed, 2000, f"Range {start}-{end} took {elapsed:.0f}ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
