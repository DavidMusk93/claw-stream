#!/usr/bin/env python3
"""Test helper API routes.

Provide simple HTTP endpoints so regression tests can create synthetic data via API,
call internal functions, thus eliminating the need for real SNOS-171/EBWH-322 cache files.

For development/test environments only; should not be exposed in production.
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

# Default to cache/torrent under the project root
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_CACHE_DIR = os.path.join(_SCRIPT_DIR, "cache", "torrent")


class SyntheticCreateRequest(BaseModel):
    hash: str
    moov_position: str = "head"  # "head" or "tail"
    logic_size: int = 32 * 1024 * 1024  # default 32MB


class SyntheticCreateResponse(BaseModel):
    hash: str
    path: str
    moov_position: str
    logic_size: int


@router.post("/create-synthetic", response_model=SyntheticCreateResponse)
async def create_synthetic(req: SyntheticCreateRequest):
    """Create a synthetic sparse MP4 file under cache/torrent/{hash}.

    Return the created file path for subsequent use by the stream / check API.
    """
    cache_dir = _DEFAULT_CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)

    # Import synthetic_mp4 module (tests directory is on PYTHONPATH)
    try:
        from tests.synthetic_mp4 import _build_moov_box
    except ImportError:
        # If tests is not on PYTHONPATH, try relative import
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
    """Delete synthetic files under cache/torrent/{hash}."""
    cache_dir = _DEFAULT_CACHE_DIR
    target = os.path.join(cache_dir, hash_str)
    if os.path.exists(target):
        shutil.rmtree(target, ignore_errors=True)
        return {"hash": hash_str, "removed": True}
    return {"hash": hash_str, "removed": False}


@router.get("/scan-moov")
async def api_scan_moov(path: str):
    """API wrapper for _scan_mp4_moov: scan the moov position of an MP4."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    moov_start, moov_end = _scan_mp4_moov(path)
    return {"path": path, "moov_start": moov_start, "moov_end": moov_end}


@router.get("/range-has-data")
async def api_range_has_data(path: str, start: int, end: int):
    """API wrapper for _range_has_data: check whether the [start, end] range has actual data."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    result = _range_has_data(path, start, end)
    return {"path": path, "start": start, "end": end, "has_data": result}


@router.get("/is-data-at-offset")
async def api_is_data_at_offset(path: str, offset: int):
    """API wrapper for _is_data_at_offset: check whether there is actual data at offset."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    result = _is_data_at_offset(path, offset)
    return {"path": path, "offset": offset, "is_data": result}


@router.get("/find-video-state/{hash_str}")
async def api_find_video_state(hash_str: str):
    """API wrapper for find_video_state: look up video state."""
    path, real_size, head_ready, mime = find_video_state(hash_str)
    return {
        "hash": hash_str,
        "path": path,
        "real_size": real_size,
        "head_ready": head_ready,
        "mime": mime,
    }
