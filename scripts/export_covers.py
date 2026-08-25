#!/usr/bin/env python3
"""scripts/export_covers.py — Export title covers from DuckDB to disk.

First-principle rationale:
Covers are immutable static assets. Keeping them as base64 blobs inside DuckDB
forces every cover request to execute SQL, read the blob, and base64-decode it.
Exporting them to disk lets the web server serve files directly and lets the
browser cache them efficiently.

Output layout:
    images/titles/{code_lower}/{code_lower}.jpg
    images/titles/{code_lower}/{code_lower}_thumb.jpg

All images are normalized to JPEG so the frontend can construct deterministic
URLs without needing to know the original format. Each cover also gets a small
thumbnail (THUMB_WIDTH px wide) used by list/grid views so the browser does not
download the full-size cover for a ~180px thumbnail.
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
THUMB_WIDTH = 400
THUMB_QUALITY = 82


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


def _make_thumb_jpeg(img: Image.Image) -> bytes:
    """Resize an open image to THUMB_WIDTH px wide and encode as JPEG."""
    if img.width > THUMB_WIDTH:
        height = round(img.height * THUMB_WIDTH / img.width)
        img = img.resize((THUMB_WIDTH, height), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return out.getvalue()


def _ensure_thumb(code_lower: str, b64_data: str) -> bool:
    """Generate {code}_thumb.jpg if missing. Source: existing full-size JPEG,
    falling back to the DuckDB blob. Returns True when the thumb exists."""
    thumb_path = IMAGES_DIR / code_lower / f"{code_lower}_thumb.jpg"
    if thumb_path.exists() and thumb_path.stat().st_size > 0:
        return True
    try:
        full_path = IMAGES_DIR / code_lower / f"{code_lower}.jpg"
        if full_path.exists() and full_path.stat().st_size > 0:
            img = Image.open(full_path)
        else:
            img = Image.open(io.BytesIO(_decode_b64(b64_data)))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(_make_thumb_jpeg(img))
        return True
    except Exception as exc:
        print(f"Failed to generate thumb for {code_lower}: {exc}", file=sys.stderr)
        return False


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

    stats = {"total": len(rows), "exported": 0, "skipped": 0, "failed": 0, "thumbs": 0}
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    for code, b64_data in rows:
        code_lower = code.lower()
        out_dir = IMAGES_DIR / code_lower
        out_path = out_dir / f"{code_lower}.jpg"

        if out_path.exists() and out_path.stat().st_size > 0:
            stats["skipped"] += 1
        else:
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
                continue

        if _ensure_thumb(code_lower, b64_data):
            stats["thumbs"] += 1

    return stats


def main() -> int:
    stats = export_covers()
    print(
        f"Cover export complete: "
        f"total={stats['total']}, exported={stats['exported']}, "
        f"skipped={stats['skipped']}, failed={stats['failed']}, "
        f"thumbs={stats['thumbs']}"
    )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
