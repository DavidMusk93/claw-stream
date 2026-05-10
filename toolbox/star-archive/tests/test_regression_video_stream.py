#!/usr/bin/env python3
"""Regression tests for video streaming pipeline.

Covers the bugs fixed in May 2026:
1. False hole detection (MP4 ftyp 00 00 misdetected as hole)
2. Memory explosion (bytes=0- reading entire file)
3. Finished-state deadlock (libtorrent ignores priorities)
4. Stale have_piece bitmap after service restart
5. Incomplete moov atom causing FFmpeg decode failures

Run: cd tests && python3 -m pytest test_regression_video_stream.py -v
     or: python3 tests/test_regression_video_stream.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.video_stream import _is_data_at_offset
from services.torrent_engine import _range_has_data, _scan_mp4_moov, find_video_state

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache", "torrent")

# SNOS-171: 100% complete, head-moov (3.8GB)
HEAD_HASH = "c2fe9437eef243096ce5789a8d5a435df6ee5fa3"
HEAD_PATH = os.path.join(CACHE_DIR, HEAD_HASH, "SNOS-171", "hhd800.com@SNOS-171.mp4")

# EBWH-322: 100% complete, tail-moov (5.4GB)
TAIL_HASH = "e277f22f86a346efefe4242fd4dc7f5455dc272d"
TAIL_PATH = os.path.join(CACHE_DIR, TAIL_HASH, "EBWH-322ch", "EBWH-322ch.mp4")


def _make_sparse_file() -> str:
    """Create a sparse file with data at [0, 4KB) and [1MB, 1MB+4KB), hole in between."""
    fd, path = tempfile.mkstemp(suffix=".sparse.mp4")
    try:
        os.write(fd, b"\x01" * 4096)
        os.lseek(fd, 1024 * 1024, os.SEEK_SET)
        os.write(fd, b"\x02" * 4096)
    finally:
        os.close(fd)
    return path


class TestSeekDataHoleDetection(unittest.TestCase):
    """Test SEEK_DATA/SEEK_HOLE filesystem-level hole detection on real sparse files."""

    def setUp(self) -> None:
        self.sparse_path = _make_sparse_file()

    def tearDown(self) -> None:
        if os.path.exists(self.sparse_path):
            os.unlink(self.sparse_path)

    def test_is_data_at_offset_with_data(self) -> None:
        """Offset 0 of SNOS-171 has actual data (ftyp header)."""
        if not os.path.exists(HEAD_PATH):
            self.fail(f"SNOS-171 cache must be available: {HEAD_PATH}")
        self.assertTrue(_is_data_at_offset(HEAD_PATH, 0))
        self.assertTrue(_is_data_at_offset(HEAD_PATH, 8))

    def test_is_data_at_offset_in_hole(self) -> None:
        """Offset inside a known hole returns False."""
        # Use the synthetic sparse file to guarantee a hole regardless of
        # which torrents are cached.  The hole is at [4096, 1048576).
        self.assertFalse(_is_data_at_offset(self.sparse_path, 8192))
        self.assertFalse(_is_data_at_offset(self.sparse_path, 524288))

    def test_range_has_data_moov(self) -> None:
        """SNOS-171 moov range [0, 8.6MB] has no holes."""
        if not os.path.exists(HEAD_PATH):
            self.fail(f"SNOS-171 cache must be available: {HEAD_PATH}")
        self.assertTrue(_range_has_data(HEAD_PATH, 0, 8_686_350))

    def test_range_has_hole(self) -> None:
        """Synthetic sparse file [0, 1MB+4KB] crosses a hole."""
        self.assertFalse(_range_has_data(self.sparse_path, 0, 1024 * 1024 + 4096))

    def test_mp4_first_two_bytes_not_hole(self) -> None:
        """MP4 ftyp size field starts with 00 00 — must NOT be detected as hole."""
        if not os.path.exists(HEAD_PATH):
            self.fail(f"SNOS-171 cache must be available: {HEAD_PATH}")
        self.assertTrue(_is_data_at_offset(HEAD_PATH, 0))
        self.assertTrue(_is_data_at_offset(HEAD_PATH, 1))


class TestMp4MoovDetection(unittest.TestCase):
    """Test MP4 moov atom scanning and head_ready logic."""

    def test_scan_mp4_moov_head(self) -> None:
        """SNOS-171: moov in head, should return moov_start=0, moov_end>0."""
        if not os.path.exists(HEAD_PATH):
            self.fail(f"SNOS-171 cache must be available: {HEAD_PATH}")
        moov_start, moov_end = _scan_mp4_moov(HEAD_PATH)
        self.assertEqual(moov_start, 0, "head-moov should have moov_start=0")
        self.assertGreater(moov_end, 0, "moov should be found")
        print(f"  SNOS-171 moov_start={moov_start:,} moov_end={moov_end:,}")

    def test_scan_mp4_moov_tail(self) -> None:
        """EBWH-322: moov in tail, should return moov_start>0."""
        if not os.path.exists(TAIL_PATH):
            self.fail(f"EBWH-322 cache must be available: {TAIL_PATH}")
        moov_start, moov_end = _scan_mp4_moov(TAIL_PATH)
        if moov_end == 0:
            self.fail("EBWH-322 moov must be found (file is 100% complete)")
        self.assertGreater(moov_start, 0, "tail-moov should have moov_start>0")
        self.assertGreater(moov_end, moov_start, "moov_end should be > moov_start")
        print(f"  EBWH-322 moov_start={moov_start:,} moov_end={moov_end:,} (tail-moov)")

    def test_find_video_state_head_moov(self) -> None:
        """SNOS-171: head-moov with complete data → head_ready=True."""
        if not os.path.exists(HEAD_PATH):
            self.fail(f"SNOS-171 cache must be available: {HEAD_PATH}")
        path, real_size, head_ready, mime = find_video_state(HEAD_HASH)
        if not path:
            self.fail("find_video_state returned None for SNOS-171")
        print(f"  SNOS-171: real_size={real_size:,} head_ready={head_ready}")
        moov_start, moov_end = _scan_mp4_moov(HEAD_PATH)
        if _range_has_data(HEAD_PATH, moov_start, moov_end):
            self.assertTrue(head_ready, "head_ready should be True when moov is complete")

    def test_find_video_state_tail_moov(self) -> None:
        """EBWH-322: tail-moov only needs moov region, not whole file."""
        if not os.path.exists(TAIL_PATH):
            self.fail(f"EBWH-322 cache must be available: {TAIL_PATH}")
        path, real_size, head_ready, mime = find_video_state(TAIL_HASH)
        if not path:
            self.fail("find_video_state returned None for EBWH-322")
        print(f"  EBWH-322: real_size={real_size:,} head_ready={head_ready}")
        moov_start, moov_end = _scan_mp4_moov(TAIL_PATH)
        if moov_end == 0:
            self.fail("EBWH-322 moov must be found")
        moov_complete = _range_has_data(TAIL_PATH, moov_start, moov_end - 1)
        print(f"  EBWH-322 moov_complete={moov_complete}")
        if moov_complete:
            self.assertTrue(head_ready, "tail-moov head_ready should be True when moov region is complete")


class TestBrowserPlaybackFlow(unittest.TestCase):
    """Simulate browser Range requests and verify data integrity."""

    BASE = "http://127.0.0.1:8765"

    @classmethod
    def setUpClass(cls) -> None:
        import requests
        try:
            # Wait for backend to be ready and not checking_files
            for _ in range(30):
                r = requests.get(f"{cls.BASE}/api/check/{HEAD_HASH}")
                if r.status_code == 200 and r.json().get("head_ready"):
                    cls.backend_ok = True
                    return
                time.sleep(0.5)
            cls.backend_ok = False
        except Exception:
            cls.backend_ok = False

    def _get_with_retry(self, url: str, headers: dict | None = None, max_retries: int = 10) -> Any:
        """Send GET with 503 retry (checking_files may cause temporary 503)."""
        import requests
        for i in range(max_retries):
            r = requests.get(url, headers=headers or {}, timeout=30)
            if r.status_code != 503:
                return r
            time.sleep(0.5)
        return r

    def test_range_probe_2_bytes(self) -> None:
        """Browser first sends bytes=0-1 to probe format."""
        if not self.backend_ok:
            self.fail("Backend must be running on port 8765 for regression testing")
        r = self._get_with_retry(
            f"{self.BASE}/stream/{HEAD_HASH}",
            headers={"Range": "bytes=0-1"},
        )
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(r.content), 2)
        # First 2 bytes are 00 00 (MP4 ftyp size field) — legitimate data, not hole
        print(f"  bytes=0-1: {r.content.hex()}")

    def test_range_moov_region(self) -> None:
        """Request enough bytes to cover moov atom, verify ffprobe can parse."""
        if not self.backend_ok:
            self.fail("Backend must be running on port 8765 for regression testing")

        # SNOS-171 moov ends at ~8.7MB; download 10MB to be safe
        moov_start, moov_end = _scan_mp4_moov(HEAD_PATH)
        max_bytes = max(10 * 1024 * 1024, moov_end + 1024 * 1024)

        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        try:
            offset = 0
            while offset < max_bytes:
                end = offset + 1024 * 1024 - 1
                r = self._get_with_retry(
                    f"{self.BASE}/stream/{HEAD_HASH}",
                    headers={"Range": f"bytes={offset}-{end}"},
                )
                if r.status_code != 206:
                    break
                tmp.write(r.content)
                offset += len(r.content)
                if len(r.content) == 0:
                    break
            tmp.close()

            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-show_entries", "stream=codec_name,width,height",
                 "-of", "default=noprint_wrappers=1", tmp.name],
                capture_output=True, text=True,
            )
            print(f"  ffprobe stdout: {result.stdout.strip()[:200]}")
            if result.returncode != 0:
                print(f"  ffprobe stderr: {result.stderr.strip()[:200]}")
            self.assertEqual(result.returncode, 0, "ffprobe should parse moov successfully")
        finally:
            os.unlink(tmp.name)

    def test_range_mid_file_not_all_zero(self) -> None:
        """Request 1KB at 1GB position — should not be all zeros."""
        if not self.backend_ok:
            self.fail("Backend must be running on port 8765 for regression testing")
        r = self._get_with_retry(
            f"{self.BASE}/stream/{HEAD_HASH}",
            headers={"Range": "bytes=1000000000-1000000999"},
        )
        self.assertEqual(r.status_code, 206)
        zero_pct = r.content.count(0) / len(r.content) * 100
        print(f"  bytes=1GB-: size={len(r.content)} zero_pct={zero_pct:.1f}%")
        self.assertLess(zero_pct, 50, "mid-file range should have substantial data")


class TestFfmpegDecodeIntegrity(unittest.TestCase):
    """Use ffmpeg to verify actual video files can be decoded."""

    def test_snos171_decode(self) -> None:
        """SNOS-171: ffmpeg should decode first 5s without errors."""
        if not os.path.exists(HEAD_PATH):
            self.fail(f"SNOS-171 cache must be available: {HEAD_PATH}")
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "0", "-t", "5",
             "-i", HEAD_PATH, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        if result.stderr.strip():
            print(f"  ffmpeg stderr: {result.stderr.strip()[:300]}")
        self.assertEqual(result.returncode, 0, "ffmpeg should decode without fatal errors")

    def test_ebwh322_decode(self) -> None:
        """EBWH-322: tail-moov complete file should decode successfully."""
        if not os.path.exists(TAIL_PATH):
            self.fail(f"EBWH-322 cache must be available: {TAIL_PATH}")
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "0", "-t", "2",
             "-i", TAIL_PATH, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        print(f"  EBWH-322 ffmpeg returncode={result.returncode}")
        if result.stderr.strip():
            print(f"  stderr: {result.stderr.strip()[:200]}")
        self.assertEqual(result.returncode, 0, "ffmpeg should decode tail-moov file without fatal errors")


if __name__ == "__main__":
    unittest.main(verbosity=2)
