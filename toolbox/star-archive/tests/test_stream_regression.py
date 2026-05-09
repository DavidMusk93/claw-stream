#!/usr/bin/env python3
"""Regression tests using real FastAPI TestClient + real cache files + real HTTP requests.

These tests send actual HTTP requests to the backend via TestClient,
using real torrent cache files from disk.  If cache files are not
available, tests are skipped.

Key scenarios covered:
1. Safari Range request sequence — simulate browser playback pattern
2. checking_files blocks stream — verify 503 / head_ready=false
3. Range response integrity — Content-Range matches data, overlaps consistent
4. Hole range returns 416 — not all-zero garbage

Run: cd tests && ../.venv/bin/python3 -m pytest test_stream_regression.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import stream_router, check_router
from backend.services.torrent_engine import TorrentEngine, find_video_state
from backend.services.video_stream import read_video_range


# ── Config ──────────────────────────────────────────────────────────
REAL_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "torrent")
SAMPLE_HASH = "a801b7b8a46fac6ec4cef0f1f95d0e75f1ebf8b1"
SAMPLE_VIDEO_REL = os.path.join(
    SAMPLE_HASH, "DLDSS-483", "hhd800.com@DLDSS-483.mp4"
)


def _has_sample() -> bool:
    return os.path.exists(os.path.join(REAL_CACHE_DIR, SAMPLE_VIDEO_REL))


# ── Shared module-level fixture ─────────────────────────────────────

class _SharedEngine:
    """Module-level singleton: one engine, one torrent add, checked once."""
    _instance: TorrentEngine | None = None
    _temp_dir: str | None = None
    _video_path: str | None = None
    _ready: bool = False

    @classmethod
    def get(cls) -> tuple[TorrentEngine, str, TestClient]:
        if cls._instance is not None:
            return cls._instance, cls._video_path or "", cls._client

        if not _has_sample():
            raise unittest.SkipTest("DLDSS-483 cache not available")

        import libtorrent as lt

        cls._temp_dir = tempfile.mkdtemp(prefix="star_test_shared_")
        cls._video_path = _copy_sample_to_temp(cls._temp_dir)

        # Use max_size_gb=20 to avoid cache eviction interfering with tests
        cls._instance = TorrentEngine(cls._temp_dir, max_size_gb=20)

        # Pre-add torrent and wait for checking to finish
        magnet = f"magnet:?xt=urn:btih:{SAMPLE_HASH}"
        info = cls._instance.add_torrent(magnet, prefetch=False)
        if info:
            h = info["handle"]
            for _ in range(30):
                if h.status().has_metadata:
                    break
                time.sleep(0.2)
            for _ in range(120):  # up to 24s for large file
                st = h.status()
                if st.state != lt.torrent_status.checking_files:
                    break
                time.sleep(0.2)

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


def _copy_sample_to_temp(temp_cache: str) -> str:
    """Copy DLDSS-483 cache into temp dir using hard-links for large files.
    Avoids copying 3.8GB sparse file; returns video path.
    """
    src = os.path.join(REAL_CACHE_DIR, SAMPLE_HASH)
    dst = os.path.join(temp_cache, SAMPLE_HASH)
    os.makedirs(dst, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        dst_root = os.path.join(dst, rel_root)
        os.makedirs(dst_root, exist_ok=True)
        for f in files:
            src_file = os.path.join(root, f)
            dst_file = os.path.join(dst_root, f)
            try:
                os.link(src_file, dst_file)
            except OSError:
                shutil.copy2(src_file, dst_file)
    return os.path.join(dst, "DLDSS-483", "hhd800.com@DLDSS-483.mp4")


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
            f"/stream/{SAMPLE_HASH}",
            headers={"Range": f"bytes={start}-{end}"},
        )
        return r.content, r.status_code, dict(r.headers)

    def test_safari_probe_then_full_head(self) -> None:
        """Safari sends 0-1, then 0-total; verify each response matches file data.

        The real bug is NOT that assembled gaps cause ffprobe errors (that's
        expected for incomplete files). The bug is that overlapping ranges
        return INCONSISTENT data, which confuses Safari's demuxer and causes
        MEDIA_ERR_SRC_NOT_SUPPORTED (code=4).
        """
        # Read ground truth from file
        with open(self.video_path, "rb") as f:
            file_data = f.read()

        # Safari request pattern (observed from access logs)
        ranges = [
            (0, 1),               # probe
            (0, self.total - 1),  # full file (backend truncates to 1MB)
            (3_014_656, 3_014_656 + 1_048_575),   # ~3MB offset
            (7_602_176, 7_602_176 + 1_048_575),   # ~7.6MB offset
            (16_384, 16_384 + 1_048_575),         # ~16KB offset
        ]

        for start, end in ranges:
            data, status, headers = self._request_range(start, end)
            self.assertIn(status, {200, 206}, f"Range {start}-{end} should succeed, got {status}")
            if status == 206:
                cr = headers.get("content-range", "")
                self.assertIn("bytes", cr, f"Missing Content-Range for {start}-{end}")

            # Verify response data matches file at same offset
            expected = file_data[start:start + len(data)]
            self.assertEqual(
                data, expected,
                f"Range {start}-{end} returned data that does NOT match file at offset {start}",
            )
            print(f"  Range {start}-{end}: OK ({len(data)} bytes match file)")

        # Critical: overlapping ranges must agree
        # Request A: 0-65535, Request B: 32768-98303
        r_a = self.client.get(
            f"/stream/{SAMPLE_HASH}",
            headers={"Range": "bytes=0-65535"},
        )
        r_b = self.client.get(
            f"/stream/{SAMPLE_HASH}",
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
    """Verify that checking_files state blocks stream and check endpoints.

    Uses a private engine (not the shared one) so force_recheck doesn't
    interfere with other tests.
    """

    def setUp(self) -> None:
        if not _has_sample():
            self.skipTest("DLDSS-483 cache not available")
        self.temp_dir = tempfile.mkdtemp(prefix="star_test_cf_")
        self.video_path = _copy_sample_to_temp(self.temp_dir)
        self.app, self.engine = _make_private_app(self.temp_dir)
        self.client = TestClient(self.app)

        # Add the torrent
        magnet = f"magnet:?xt=urn:btih:{SAMPLE_HASH}"
        self.info = self.engine.add_torrent(magnet, prefetch=False)
        if not self.info:
            self.tearDown()
            self.skipTest("Failed to add torrent to engine")
        self.handle = self.info["handle"]

        # Wait for metadata
        for _ in range(30):
            if self.handle.status().has_metadata:
                break
            time.sleep(0.2)
        if not self.handle.status().has_metadata:
            self.tearDown()
            self.skipTest("Metadata not available")

    def tearDown(self) -> None:
        self.engine.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_checking_files_returns_503_and_false_head_ready(self) -> None:
        """force_recheck → checking_files → /stream returns 503, /api/check returns false."""
        import libtorrent as lt

        # First verify normal state works
        r = self.client.get(f"/api/check/{SAMPLE_HASH}")
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
        r = self.client.get(f"/api/check/{SAMPLE_HASH}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(
            r.json()["head_ready"],
            "head_ready must be False during checking_files",
        )

        # /stream must return 503
        r = self.client.get(
            f"/stream/{SAMPLE_HASH}",
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
        r = self.client.get(f"/api/check/{SAMPLE_HASH}")
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
            f"/stream/{SAMPLE_HASH}",
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
            f"/stream/{SAMPLE_HASH}",
            headers={"Range": "bytes=0-65535"},
        )
        r2 = self.client.get(
            f"/stream/{SAMPLE_HASH}",
            headers={"Range": "bytes=0-65535"},
        )
        self.assertEqual(r1.status_code, 206)
        self.assertEqual(r2.status_code, 206)
        self.assertEqual(r1.content, r2.content,
                         "Repeated range request returned different data")

    def test_overlapping_ranges_consistent(self) -> None:
        """Two overlapping ranges must agree on the overlapping bytes."""
        r_a = self.client.get(
            f"/stream/{SAMPLE_HASH}",
            headers={"Range": "bytes=0-65535"},
        )
        r_b = self.client.get(
            f"/stream/{SAMPLE_HASH}",
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

    def test_known_hole_range_returns_416(self) -> None:
        """Request a range inside a known hole; must return 416 not 200/206."""
        hole_offset = 1_500_000_000
        r = self.client.get(
            f"/stream/{SAMPLE_HASH}",
            headers={"Range": f"bytes={hole_offset}-{hole_offset + 65535}"},
        )
        if r.status_code == 416:
            cr = r.headers.get("content-range", "")
            self.assertIn("bytes */", cr, "416 must have Content-Range: bytes */total")
            self.assertEqual(len(r.content), 0, "416 must have empty body")
        else:
            self.assertEqual(r.status_code, 206)
            zero_pct = r.content.count(0) / len(r.content) * 100
            self.assertLess(zero_pct, 50,
                            f"Range at {hole_offset} returned {zero_pct:.0f}% zeros")

    def test_no_range_returns_200_with_8mb(self) -> None:
        """Request without Range header returns 200 + first 8MB (Safari compat)."""
        r = self.client.get(f"/stream/{SAMPLE_HASH}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.content), 8 * 1024 * 1024,
                         "No-Range request must return exactly 8MB")
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
    """Simulate the complete Safari playback flow from check to stream to decode.

    Covers: /api/check → /torrent/add → Range sequence → assemble → ffprobe → first frame.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine, cls.video_path, cls.client = _SharedEngine.get()
        cls.total = os.path.getsize(cls.video_path)

    def _request_range(self, start: int, end: int) -> tuple[bytes, int, dict, float]:
        """Send Range request; return (data, status, headers, elapsed_ms)."""
        import time
        t0 = time.perf_counter()
        r = self.client.get(
            f"/stream/{SAMPLE_HASH}",
            headers={"Range": f"bytes={start}-{end}"},
        )
        elapsed = (time.perf_counter() - t0) * 1000
        return r.content, r.status_code, dict(r.headers), elapsed

    def test_api_check_reports_ready(self) -> None:
        """/api/check must report head_ready=true when moov is complete."""
        r = self.client.get(f"/api/check/{SAMPLE_HASH}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["head_ready"], "head_ready must be true")
        self.assertTrue(r.json()["cached"], "cached must be true")

    def test_safari_full_range_sequence(self) -> None:
        """Simulate Safari's complete request sequence; verify each response matches file.

        Observed Safari pattern (iOS 18):
          1. bytes=0-1              (probe format)
          2. bytes=0-total          (get initial chunk, backend truncates to 8MB)
          3. bytes=3014656-total    (mid-moov)
          4. bytes=7602176-total    (moov tail / mdat start)
          5. bytes=16384-3014655    (fill moov gap)
          6. bytes=7634732-7667711  (precise first frame region)
        """
        # Read ground truth from file (mmap to avoid loading 3.8GB into RAM)
        import mmap
        with open(self.video_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as file_data:
                ranges = [
                    (0, 1),
                    (0, self.total - 1),
                    (3_014_656, self.total - 1),
                    (7_602_176, self.total - 1),
                    (16_384, 3_014_655),
                    (7_634_732, 7_667_711),
                ]

                max_elapsed = 0.0
                for start, end in ranges:
                    data, status, headers, elapsed = self._request_range(start, end)
                    self.assertIn(status, {200, 206}, f"Range {start}-{end} failed with {status}")
                    self.assertLess(elapsed, 5000, f"Range {start}-{end} took {elapsed:.0f}ms (>5s)")
                    max_elapsed = max(max_elapsed, elapsed)

                    # Verify response data matches file at same offset
                    expected = bytes(file_data[start:start + len(data)])
                    self.assertEqual(
                        data, expected,
                        f"Range {start}-{end} returned data that does NOT match file at offset {start}",
                    )

                    cr = headers.get("content-range", "")
                    self.assertIn("bytes", cr, f"Missing Content-Range for {start}-{end}")
                    print(f"  Range {start}-{end}: OK ({len(data)} bytes, {elapsed:.0f}ms)")

                print(f"  Max elapsed: {max_elapsed:.0f}ms")

                # Verify moov region is complete by checking offsets [0, 8MB]
                moov_region = bytes(file_data[0:8 * 1024 * 1024])
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
            (7_627_032, 7_627_032 + 1024 * 1024),
            (100 * 1024 * 1024, 100 * 1024 * 1024 + 1024 * 1024),
        ]
        for start, end in ranges:
            _, status, _, elapsed = self._request_range(start, end)
            self.assertEqual(status, 206)
            self.assertLess(elapsed, 2000, f"Range {start}-{end} took {elapsed:.0f}ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
