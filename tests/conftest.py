#!/usr/bin/env python3
"""Shared fixtures for star-archive regression tests.

提供本地 BitTorrent seed fixture，让回归测试基于真实的 BT 下载视频运行，
而非依赖外部网络或合成文件。
"""
from __future__ import annotations

import os
import pytest

from tests.local_bt_fixture import LocalSeed, download_with_engine, cleanup_cache_dir


@pytest.fixture(scope="session")
def local_seed():
    """启动本地 seed 会话，提供真实的 BT 种子。

    Yields a LocalSeed instance with attributes:
        - hash: str
        - listen_port: int
        - ti: libtorrent.torrent_info
    """
    seed = LocalSeed()
    try:
        yield seed
    finally:
        seed.stop()


@pytest.fixture(scope="session")
def real_video_engine(local_seed, tmp_path_factory):
    """使用 TorrentEngine 从本地 seed 下载真实视频。

    返回 (engine, video_path, hash_str)。
    engine 在 session 结束时自动关闭并清理缓存。
    """
    cache_dir = str(tmp_path_factory.mktemp("bt_cache"))
    hash_str = local_seed.hash
    seed_port = local_seed.listen_port

    engine, video_path = download_with_engine(cache_dir, hash_str, seed_port, timeout=60.0)

    yield engine, video_path, hash_str

    engine.shutdown()
    cleanup_cache_dir(cache_dir, hash_str)
