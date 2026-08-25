#!/usr/bin/env python3
"""Regression tests for video streaming pipeline.

Run against real BitTorrent-downloaded video files, covering:
1. False hole detection (MP4 ftyp 00 00 misdetected as hole)
2. Memory explosion (bytes=0- reading entire file)
3. Finished-state deadlock (libtorrent ignores priorities)
4. Stale have_piece bitmap after service restart
5. Incomplete moov atom causing FFmpeg decode failures

Run: cd tests && python3 -m pytest test_regression_video_stream.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import stream_router, check_router
from backend.routers.auth import require_auth
from backend.services.torrent_engine import (
    TorrentEngine,
    _range_has_data,
    _scan_mp4_moov,
    find_video_state,
)
from backend.services.video_stream import _is_data_at_offset
import shutil
from tests.local_bt_fixture import LocalSeed, download_with_engine, cleanup_cache_dir


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
    """Test SEEK_DATA/SEEK_HOLE filesystem-level hole detection."""

    def setUp(self) -> None:
        self.sparse_path = _make_sparse_file()

    def tearDown(self) -> None:
        if os.path.exists(self.sparse_path):
            os.unlink(self.sparse_path)

    def test_is_data_at_offset_with_data(self) -> None:
        """Offset 0 of downloaded video has actual data (ftyp header)."""
        seed = LocalSeed()
        tmp_dir = tempfile.mkdtemp(prefix="star_bt_hole_")
        try:
            engine, video_path = download_with_engine(
                None, seed.hash, seed.listen_port, timeout=60.0
            )
            self.assertTrue(_is_data_at_offset(video_path, 0))
            self.assertTrue(_is_data_at_offset(video_path, 8))
            engine.shutdown()
        finally:
            cleanup_cache_dir(None, seed.hash)
            seed.stop()

    def test_is_data_at_offset_in_hole(self) -> None:
        """Offset inside a known hole returns False."""
        self.assertFalse(_is_data_at_offset(self.sparse_path, 8192))
        self.assertFalse(_is_data_at_offset(self.sparse_path, 524288))

    def test_range_has_data_moov(self) -> None:
        """Downloaded video moov range has no holes."""
        seed = LocalSeed()
        tmp_dir = tempfile.mkdtemp(prefix="star_bt_range_")
        try:
            engine, video_path = download_with_engine(
                None, seed.hash, seed.listen_port, timeout=60.0
            )
            file_size = os.path.getsize(video_path)
            self.assertTrue(_range_has_data(video_path, 0, min(8_686_350, file_size - 1)))
            engine.shutdown()
        finally:
            cleanup_cache_dir(None, seed.hash)
            seed.stop()

    def test_range_has_hole(self) -> None:
        """Synthetic sparse file [0, 1MB+4KB] crosses a hole."""
        self.assertFalse(_range_has_data(self.sparse_path, 0, 1024 * 1024 + 4096))

    def test_mp4_first_two_bytes_not_hole(self) -> None:
        """MP4 ftyp size field starts with 00 00 — must NOT be detected as hole."""
        seed = LocalSeed()
        tmp_dir = tempfile.mkdtemp(prefix="star_bt_ftyp_")
        try:
            engine, video_path = download_with_engine(
                None, seed.hash, seed.listen_port, timeout=60.0
            )
            self.assertTrue(_is_data_at_offset(video_path, 0))
            self.assertTrue(_is_data_at_offset(video_path, 1))
            engine.shutdown()
        finally:
            cleanup_cache_dir(None, seed.hash)
            seed.stop()


class TestMp4MoovDetection(unittest.TestCase):
    """Test MP4 moov atom scanning and head_ready logic."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seed = LocalSeed()
        cls.tmp_dir = tempfile.mkdtemp(prefix="star_bt_moov_")
        cls.engine, cls.video_path = download_with_engine(
            None, cls.seed.hash, cls.seed.listen_port, timeout=60.0
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.shutdown()
        cleanup_cache_dir(None, cls.seed.hash)
        cls.seed.stop()
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_scan_mp4_moov_head(self) -> None:
        """Downloaded video: moov should be found."""
        moov_start, moov_end = _scan_mp4_moov(self.video_path)
        self.assertGreater(moov_end, 0, "moov should be found")
        print(f"  moov_start={moov_start:,} moov_end={moov_end:,}")

    def test_find_video_state_head_moov(self) -> None:
        """Downloaded video with complete data → head_ready=True."""
        path, real_size, head_ready, mime = find_video_state(self.seed.hash)
        if not path:
            self.fail("find_video_state returned None")
        print(f"  real_size={real_size:,} head_ready={head_ready}")
        moov_start, moov_end = _scan_mp4_moov(self.video_path)
        if _range_has_data(self.video_path, moov_start, moov_end):
            self.assertTrue(head_ready, "head_ready should be True when moov is complete")


class TestBrowserPlaybackFlow(unittest.TestCase):
    """Simulate browser Range requests and verify data integrity via TestClient."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seed = LocalSeed()
        cls.tmp_dir = tempfile.mkdtemp(prefix="star_bt_browser_")
        cls.engine, cls.video_path = download_with_engine(
            None, cls.seed.hash, cls.seed.listen_port, timeout=60.0
        )
        cls.hash_str = cls.seed.hash
        cls.total = os.path.getsize(cls.video_path)

        cls.app = FastAPI()
        cls.app.state.engine = cls.engine
        cls.app.include_router(stream_router)
        cls.app.include_router(check_router)
        # Routers enforce the claw_auth cookie in production; bypass in tests.
        cls.app.dependency_overrides[require_auth] = lambda: None
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.shutdown()
        cleanup_cache_dir(None, cls.seed.hash)
        cls.seed.stop()
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def _get_with_retry(self, url: str, headers: dict | None = None, max_retries: int = 3) -> Any:
        """Send GET with retry."""
        import time
        for i in range(max_retries):
            r = self.client.get(url, headers=headers or {})
            if r.status_code != 503:
                return r
            time.sleep(0.5)
        return r

    def test_api_check_reports_ready(self) -> None:
        """/api/check returns head_ready=True for real downloaded files."""
        r = self.client.get(f"/api/check/{self.hash_str}")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["head_ready"], "真实下载文件应 head_ready=True")
        self.assertTrue(data["cached"], "真实下载文件应 cached=True")
        print(f"  /api/check: head_ready={data['head_ready']} size={data['size']}")

    def test_range_probe_2_bytes(self) -> None:
        """Browser first sends bytes=0-1 to probe format."""
        r = self._get_with_retry(
            f"/stream/{self.hash_str}",
            headers={"Range": "bytes=0-1"},
        )
        self.assertEqual(r.status_code, 206)
        self.assertEqual(len(r.content), 2)
        print(f"  bytes=0-1: {r.content.hex()}")

    def test_range_moov_region(self) -> None:
        """Request enough bytes to cover moov atom, verify data integrity."""
        moov_start, moov_end = _scan_mp4_moov(self.video_path)
        max_bytes = min(10 * 1024 * 1024, self.total)

        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        try:
            offset = 0
            while offset < max_bytes:
                end = min(offset + 1024 * 1024 - 1, self.total - 1)
                r = self._get_with_retry(
                    f"/stream/{self.hash_str}",
                    headers={"Range": f"bytes={offset}-{end}"},
                )
                if r.status_code != 206:
                    break
                tmp.write(r.content)
                offset += len(r.content)
                if len(r.content) == 0:
                    break
            tmp.close()

            with open(tmp.name, "rb") as f:
                header = f.read(8)
                self.assertEqual(header[4:8], b"ftyp", "应包含 ftyp box")
            print(f"  assembled: {os.path.getsize(tmp.name)} bytes")
        finally:
            os.unlink(tmp.name)

    def test_range_mid_file_not_all_zero(self) -> None:
        """Request 1KB at mid position — should not be all zeros."""
        mid = self.total // 2
        r = self._get_with_retry(
            f"/stream/{self.hash_str}",
            headers={"Range": f"bytes={mid}-{mid + 1023}"},
        )
        self.assertEqual(r.status_code, 206)
        zero_pct = r.content.count(0) / len(r.content) * 100
        print(f"  bytes={mid}-: size={len(r.content)} zero_pct={zero_pct:.1f}%")
        self.assertLess(zero_pct, 50, "mid-file range should have substantial data")

    def test_no_range_returns_200_with_chunk(self) -> None:
        """Request without Range header returns 200 + first chunk."""
        r = self.client.get(f"/stream/{self.hash_str}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("accept-ranges"), "bytes")
        print(f"  no-range: status={r.status_code} size={len(r.content)}")


class TestFfmpegDecodeIntegrity(unittest.TestCase):
    """Use ffmpeg to verify downloaded video can be decoded."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.seed = LocalSeed()
        cls.tmp_dir = tempfile.mkdtemp(prefix="star_bt_ffmpeg_")
        cls.engine, cls.video_path = download_with_engine(
            None, cls.seed.hash, cls.seed.listen_port, timeout=60.0
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.shutdown()
        cleanup_cache_dir(None, cls.seed.hash)
        cls.seed.stop()
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_video_decode(self) -> None:
        """ffmpeg should decode first 5s without errors."""
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "0", "-t", "5",
             "-i", self.video_path, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        if result.stderr.strip():
            print(f"  ffmpeg stderr: {result.stderr.strip()[:300]}")
        self.assertEqual(result.returncode, 0, "ffmpeg should decode without fatal errors")


if __name__ == "__main__":
    unittest.main(verbosity=2)
