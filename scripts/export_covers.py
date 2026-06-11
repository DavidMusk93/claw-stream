#!/usr/bin/env python3
"""scripts/export_covers.py — Export title covers from DuckDB to disk.

First-principle rationale:
Covers are immutable static assets. Keeping them as base64 blobs inside DuckDB
forces every cover request to execute SQL, read the blob, and base64-decode it.
Exporting them to disk lets the web server serve files directly and lets the
browser cache them efficiently.

Output layout:
    images/titles/{code_lower}/{code_lower}.jpg

All images are normalized to JPEG so the frontend can construct deterministic
URLs without needing to know the original format.
"""

from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import db

SCRIPT_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = SCRIPT_DIR / "images" / "titles"
JPEG_QUALITY = 90


def _decode_b64(b64_data: str) -> bytes:
    """Strip data URI prefix and base64-decode."""
    if b64_data.startswith("data:image/"):
        b64_data = b64_data.split(",", 1)[1]
    return base64.b64decode(b64_data)


def _normalize_to_jpeg(raw_bytes: bytes) -> bytes | None:
    """Convert any Pillow-readable image to JPEG bytes."""
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        # Convert palette/RGBA to RGB to avoid JPEG mode errors
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return out.getvalue()
    except Exception:
        return None


def export_covers() -> dict[str, int]:
    """Export all covers from DuckDB to disk as JPEG."""
    db.init_schema()
    conn = db._conn()
    try:
        rows = conn.execute(
            """
            SELECT code, cover_b64
            FROM titles
            WHERE cover_b64 IS NOT NULL AND cover_b64 != ''
            ORDER BY code
            """
        ).fetchall()
    finally:
        conn.close()

    stats = {"total": len(rows), "exported": 0, "skipped": 0, "failed": 0}
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    for code, b64_data in rows:
        code_lower = code.lower()
        out_dir = IMAGES_DIR / code_lower
        out_path = out_dir / f"{code_lower}.jpg"

        if out_path.exists() and out_path.stat().st_size > 0:
            stats["skipped"] += 1
            continue

        try:
            raw_bytes = _decode_b64(b64_data)
            jpeg_bytes = _normalize_to_jpeg(raw_bytes)
            if not jpeg_bytes:
                stats["failed"] += 1
                continue

            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(jpeg_bytes)
            stats["exported"] += 1
        except Exception as exc:
            print(f"Failed to export {code}: {exc}", file=sys.stderr)
            stats["failed"] += 1

    return stats


def main() -> int:
    stats = export_covers()
    print(
        f"Cover export complete: "
        f"total={stats['total']}, exported={stats['exported']}, "
        f"skipped={stats['skipped']}, failed={stats['failed']}"
    )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
