#!/usr/bin/env python3
"""本地 BitTorrent seed + leech 辅助模块。

为回归测试提供真实的 BT 下载环境：
1. 启动本地 seed（tests/fixtures/test_video.mp4）
2. 提供 magnet URI 和 hash
3. 帮助函数：让 TorrentEngine 从本地 seed 下载视频

不依赖外部网络，下载在 localhost 完成，通常 3-5 秒内结束。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import libtorrent as lt

from backend.services.torrent_engine import TorrentEngine, CACHE_DIR

# ── 常量 ────────────────────────────────────────────────────────────
_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_VIDEO_PATH = os.path.join(_FIXTURES_DIR, "test_video.mp4")
_TORRENT_PATH = os.path.join(_FIXTURES_DIR, "test_video.torrent")

_VIDEO_NAME = "test_video.mp4"


def _ensure_fixture() -> tuple[str, str]:
    """确保测试视频和种子文件已生成，返回 (video_path, torrent_path)。"""
    if not os.path.exists(_VIDEO_PATH):
        raise RuntimeError(
            f"测试视频不存在: {_VIDEO_PATH}\n"
            "请先用 ffmpeg 生成: \n"
            "ffmpeg -f lavfi -i testsrc=duration=60:size=1280x720:rate=30 "
            "-pix_fmt yuv420p -c:v libx264 -preset ultrafast -crf 23 "
            "-movflags +faststart tests/fixtures/test_video.mp4 -y"
        )
    if not os.path.exists(_TORRENT_PATH):
        raise RuntimeError(
            f"种子文件不存在: {_TORRENT_PATH}\n"
            "请重新生成 torrent（视频内容改变后 hash 会变）。"
        )
    return _VIDEO_PATH, _TORRENT_PATH


def _get_info_hash() -> str:
    """从种子文件读取 info hash。"""
    _, torrent_path = _ensure_fixture()
    with open(torrent_path, "rb") as f:
        ti = lt.torrent_info(lt.bdecode(f.read()))
    return str(ti.info_hash())


def _get_magnet() -> str:
    """构建 magnet URI。"""
    return f"magnet:?xt=urn:btih:{_get_info_hash()}"


class LocalSeed:
    """本地 seed 会话管理器。"""

    def __init__(self) -> None:
        self.session = lt.session()
        settings = self.session.get_settings()
        settings["alert_mask"] = int(lt.alert.category_t.status_notification)
        self.session.apply_settings(settings)

        video_path, torrent_path = _ensure_fixture()
        with open(torrent_path, "rb") as f:
            self.ti = lt.torrent_info(lt.bdecode(f.read()))

        params = lt.add_torrent_params()
        params.ti = self.ti
        params.save_path = os.path.dirname(video_path)
        params.flags |= lt.torrent_flags.seed_mode
        self.handle = self.session.add_torrent(params)

        # 等待进入 seeding 状态
        for _ in range(50):
            st = self.handle.status()
            if st.state == lt.torrent_status.seeding:
                break
            time.sleep(0.05)

        self.listen_port = self.session.listen_port()
        self.hash = str(self.ti.info_hash())

    def stop(self) -> None:
        """停止 seed 会话。"""
        self.session.remove_torrent(self.handle)
        # 给 libtorrent 一点清理时间
        time.sleep(0.2)


def download_with_engine(
    cache_dir: str | None,
    hash_str: str,
    seed_port: int,
    timeout: float = 30.0,
) -> tuple[TorrentEngine, str]:
    """用 TorrentEngine 从本地 seed 下载视频。

    cache_dir 为 None 时使用项目默认的 CACHE_DIR，确保 find_video_state 能找到文件。
    """
    if cache_dir is None:
        cache_dir = CACHE_DIR
    """用 TorrentEngine 从本地 seed 下载视频。

    返回 (engine, video_path)。video_path 在 cache_dir/hash_str/ 下。
    """
    engine = TorrentEngine(cache_dir, max_size_gb=20)

    # 添加 magnet（本地 seed，不需要 tracker）
    magnet = f"magnet:?xt=urn:btih:{hash_str}"
    info = engine.add_torrent(magnet, prefetch=False)

    if not info:
        engine.shutdown()
        raise RuntimeError("Failed to add torrent to engine")

    handle = info["handle"]

    # 手动连接本地 seed，避免 DHT 发现延迟
    handle.connect_peer(("127.0.0.1", seed_port), 0)

    # 等待 metadata
    for _ in range(int(timeout * 2)):
        if handle.status().has_metadata:
            break
        time.sleep(0.5)
    if not handle.status().has_metadata:
        engine.shutdown()
        raise RuntimeError("Timeout waiting for metadata from local seed")

    # 等待下载完成（is_seed 比 progress 更可靠）
    deadline = time.time() + timeout
    while time.time() < deadline:
        if handle.is_seed():
            break
        time.sleep(0.2)

    # 找到视频文件
    ti = handle.torrent_file()
    video_path: str | None = None
    if ti:
        fs = ti.files()
        for i in range(fs.num_files()):
            fp = os.path.join(cache_dir, hash_str, fs.file_path(i))
            if os.path.exists(fp):
                video_path = fp
                break

    if not video_path:
        # 回退扫描
        hash_dir = os.path.join(cache_dir, hash_str)
        if os.path.exists(hash_dir):
            for root, _, files in os.walk(hash_dir):
                for f in files:
                    if f.endswith(".mp4"):
                        video_path = os.path.join(root, f)
                        break

    if not video_path:
        engine.shutdown()
        raise RuntimeError(f"Video file not found after download")

    # 强制 flush 到磁盘，确保 st_blocks 反映实际数据
    # libtorrent 2.0 使用 mmap，需要显式 sync
    with open(video_path, "rb") as f:
        os.fsync(f.fileno())

    # 验证文件有实际数据（_check_video_ready 的阈值 1MB）
    st = os.stat(video_path)
    real_size = st.st_blocks * 512
    if real_size < 1024 * 1024:
        # 如果 mmap 数据还在 page cache，手动做一次预读来触发块分配
        with open(video_path, "rb") as f:
            while f.read(65536):
                pass
        st = os.stat(video_path)
        real_size = st.st_blocks * 512

    if real_size < 1024 * 1024:
        engine.shutdown()
        raise RuntimeError(
            f"Downloaded video real_size={real_size} < 1MB, "
            f"st_size={st.st_size} st_blocks={st.st_blocks}. "
            f"File may not be fully flushed to disk."
        )

    return engine, video_path


def cleanup_cache_dir(cache_dir: str | None, hash_str: str) -> None:
    """清理指定 hash 的缓存目录。"""
    if cache_dir is None:
        cache_dir = CACHE_DIR
    target = os.path.join(cache_dir, hash_str)
    if os.path.exists(target):
        shutil.rmtree(target, ignore_errors=True)
