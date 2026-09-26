#!/usr/bin/env python3
"""Fast-resume persistence regression: a graceful engine restart must not
pay the full-file hash check again.

Without a saved resume snapshot, libtorrent adds a paused torrent with
existing files into checking_files limbo, and the first play after a restart
blocks until the entire file is rehashed (tens of seconds for multi-GB
files). The engine saves resume data on graceful shutdown and merges the
have_pieces bitmask at add time, so the restarted engine is ready instantly.

Requires the local BT fixture; auto-skips otherwise.
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest

from tests.local_bt_fixture import (
    cleanup_cache_dir,
    download_with_engine,
)


@pytest.mark.usefixtures("local_seed")
class TestResumeDataPersistence:
    def test_restart_skips_hash_check(self, local_seed, tmp_path):
        from services.torrent_engine import TorrentEngine

        cache_dir = str(tmp_path / "bt_cache")
        hash_str = local_seed.hash

        engine, _video_path = download_with_engine(
            cache_dir, hash_str, local_seed.listen_port, timeout=60.0
        )
        # Graceful shutdown flushes the fast-resume snapshot to disk.
        engine.shutdown()

        resume_file = os.path.join(cache_dir, hash_str, f"{hash_str}.resume")
        assert os.path.exists(resume_file), "shutdown must flush resume data"

        # Simulate a service restart: a brand-new engine over the same cache.
        engine2 = TorrentEngine(cache_dir, max_size_gb=20)
        try:
            info = engine2.add_torrent(f"magnet:?xt=urn:btih:{hash_str}")
            assert info is not None

            # The fixture torrent is hybrid and addressed by its truncated v2
            # hash, so the metadata cache is skipped — feed metadata from the
            # local seed (same retry pattern as local_bt_fixture).
            handle = info["handle"]
            deadline = time.time() + 30
            while time.time() < deadline and not info.get("_metadata_done"):
                handle.connect_peer(("127.0.0.1", local_seed.listen_port), 0)
                time.sleep(0.5)
            assert info.get("_metadata_done"), "metadata never applied"

            status = engine2.get_status(hash_str)
            assert status["state"] not in ("checking_files", "checking_resume_data"), (
                f"fast resume must skip the hash check, got state={status['state']}"
            )
            assert status["ready"] is True
            assert status["head_ready"] is True
            assert status["verified_pieces"] > 0
        finally:
            engine2.shutdown()
            cleanup_cache_dir(cache_dir, hash_str)
