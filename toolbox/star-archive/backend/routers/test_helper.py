#!/usr/bin/env python3
"""测试辅助API路由。

提供简单的HTTP端点，让回归测试可以通过API创建合成数据、
调用内部函数，从而无需依赖真实的SNOS-171/EBWH-322缓存文件。

仅在开发/测试环境使用，生产环境不应暴露。
"""
from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.torrent_engine import _scan_mp4_moov, _range_has_data, find_video_state
from backend.services.video_stream import _is_data_at_offset
from core import get_logger

log = get_logger("test-helper")
router = APIRouter(prefix="/api/test", tags=["test"])

# 默认使用项目根目录下的 cache/torrent
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_CACHE_DIR = os.path.join(_SCRIPT_DIR, "cache", "torrent")


class SyntheticCreateRequest(BaseModel):
    hash: str
    moov_position: str = "head"  # "head" 或 "tail"
    logic_size: int = 32 * 1024 * 1024  # 默认 32MB


class SyntheticCreateResponse(BaseModel):
    hash: str
    path: str
    moov_position: str
    logic_size: int


@router.post("/create-synthetic", response_model=SyntheticCreateResponse)
async def create_synthetic(req: SyntheticCreateRequest):
    """在 cache/torrent/{hash} 下创建合成稀疏MP4文件。

    返回创建的文件路径，供后续 stream / check API 使用。
    """
    cache_dir = _DEFAULT_CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)

    # 导入 synthetic_mp4 模块（tests 目录在 PYTHONPATH 下）
    try:
        from tests.synthetic_mp4 import _build_moov_box
    except ImportError:
        # 如果 tests 不在 PYTHONPATH，尝试相对导入
        import sys
        tests_dir = os.path.join(_SCRIPT_DIR, "tests")
        if tests_dir not in sys.path:
            sys.path.insert(0, tests_dir)
        from synthetic_mp4 import _build_moov_box

    moov = _build_moov_box()
    moov_size = len(moov)

    video_dir = os.path.join(cache_dir, req.hash, "SYNTH-001")
    os.makedirs(video_dir, exist_ok=True)
    video_path = os.path.join(video_dir, "SYNTH-001.mp4")

    ftyp_data = b"isom" + (0x200).to_bytes(4, "big") + b"isom" + b"mp41"
    ftyp = (8 + len(ftyp_data)).to_bytes(4, "big") + b"ftyp" + ftyp_data

    logic_size = req.logic_size
    mdat_total_size = logic_size - len(ftyp) - moov_size
    mdat = mdat_total_size.to_bytes(4, "big") + b"mdat"

    with open(video_path, "wb") as f:
        f.write(ftyp)
        if req.moov_position == "head":
            f.write(moov)
            f.write(mdat)
            f.flush()
            os.ftruncate(f.fileno(), logic_size)
        else:  # tail
            f.write(mdat)
            f.flush()
            os.ftruncate(f.fileno(), logic_size - moov_size)
            f.seek(logic_size - moov_size)
            f.write(moov)

    log.info(f"Created synthetic torrent: hash={req.hash} path={video_path} moov={req.moov_position}")
    return SyntheticCreateResponse(
        hash=req.hash,
        path=video_path,
        moov_position=req.moov_position,
        logic_size=logic_size,
    )


@router.delete("/cleanup-synthetic/{hash_str}")
async def cleanup_synthetic(hash_str: str):
    """删除 cache/torrent/{hash} 下的合成文件。"""
    cache_dir = _DEFAULT_CACHE_DIR
    target = os.path.join(cache_dir, hash_str)
    if os.path.exists(target):
        shutil.rmtree(target, ignore_errors=True)
        return {"hash": hash_str, "removed": True}
    return {"hash": hash_str, "removed": False}


@router.get("/scan-moov")
async def api_scan_moov(path: str):
    """API封装 _scan_mp4_moov：扫描MP4的moov位置。"""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    moov_start, moov_end = _scan_mp4_moov(path)
    return {"path": path, "moov_start": moov_start, "moov_end": moov_end}


@router.get("/range-has-data")
async def api_range_has_data(path: str, start: int, end: int):
    """API封装 _range_has_data：检查[start, end]范围是否有实际数据。"""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    result = _range_has_data(path, start, end)
    return {"path": path, "start": start, "end": end, "has_data": result}


@router.get("/is-data-at-offset")
async def api_is_data_at_offset(path: str, offset: int):
    """API封装 _is_data_at_offset：检查offset处是否有实际数据。"""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    result = _is_data_at_offset(path, offset)
    return {"path": path, "offset": offset, "is_data": result}


@router.get("/find-video-state/{hash_str}")
async def api_find_video_state(hash_str: str):
    """API封装 find_video_state：查找视频状态。"""
    path, real_size, head_ready, mime = find_video_state(hash_str)
    return {
        "hash": hash_str,
        "path": path,
        "real_size": real_size,
        "head_ready": head_ready,
        "mime": mime,
    }
