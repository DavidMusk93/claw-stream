#!/usr/bin/env python3
"""scripts/export_covers.py — Export title covers from PostgreSQL to disk.

First-principle rationale:
Covers are immutable static assets. Keeping them as base64 blobs inside the
database forces every cover request to execute SQL, read the blob, and
base64-decode it. Exporting them to disk lets the web server serve files
directly and lets the browser cache them efficiently.

Output layout:
    images/titles/{code_lower}/{code_lower}.jpg
    images/titles/{code_lower}/{code_lower}_thumb.jpg
    images/titles/{code_lower}/{code_lower}_mid.jpg

All images are normalized to JPEG so the frontend can construct deterministic
URLs without needing to know the original format. Each cover also gets a small
thumbnail (THUMB_WIDTH px wide) used by list/grid views and a mid-size variant
(MID_WIDTH px wide) used by the hero via srcset, so the browser never decodes
the full-size cover for a ~440px display box.
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
MID_WIDTH = 800
MID_QUALITY = 85


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


def _make_variant_jpeg(img: Image.Image, width: int, quality: int) -> bytes:
    """Resize an open image to width px wide and encode as JPEG."""
    if img.width > width:
        height = round(img.height * width / img.width)
        img = img.resize((width, height), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def _ensure_variant(code_lower: str, b64_data: str, suffix: str, width: int, quality: int) -> bool:
    """Generate {code}{suffix}.jpg if missing. Source: existing full-size JPEG,
    falling back to the DB blob. Returns True when the variant exists."""
    variant_path = IMAGES_DIR / code_lower / f"{code_lower}{suffix}.jpg"
    if variant_path.exists() and variant_path.stat().st_size > 0:
        return True
    try:
        full_path = IMAGES_DIR / code_lower / f"{code_lower}.jpg"
        if full_path.exists() and full_path.stat().st_size > 0:
            img = Image.open(full_path)
        else:
            img = Image.open(io.BytesIO(_decode_b64(b64_data)))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        variant_path.parent.mkdir(parents=True, exist_ok=True)
        variant_path.write_bytes(_make_variant_jpeg(img, width, quality))
        return True
    except Exception as exc:
        print(f"Failed to generate {suffix} for {code_lower}: {exc}", file=sys.stderr)
        return False


def _ensure_thumb(code_lower: str, b64_data: str) -> bool:
    return _ensure_variant(code_lower, b64_data, "_thumb", THUMB_WIDTH, THUMB_QUALITY)


def _ensure_mid(code_lower: str, b64_data: str) -> bool:
    return _ensure_variant(code_lower, b64_data, "_mid", MID_WIDTH, MID_QUALITY)


def export_covers() -> dict[str, int]:
    """Export all covers from the title_covers table to disk as JPEG.

    Blobs are fetched one code at a time: loading every cover_b64 up front
    needlessly balloons memory on small machines.
    """
    db.init_schema()
    conn = db._conn()
    try:
        codes = [
            row[0]
            for row in conn.execute(
                """
                SELECT t.code
                FROM titles t
                JOIN title_covers c ON c.title_id = t.id
                WHERE c.cover_b64 IS NOT NULL AND c.cover_b64 != ''
                ORDER BY t.code
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    stats = {"total": len(codes), "exported": 0, "skipped": 0, "failed": 0, "thumbs": 0, "mids": 0}
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    conn = db._conn()
    try:
        for code in codes:
            code_lower = code.lower()
            out_dir = IMAGES_DIR / code_lower
            out_path = out_dir / f"{code_lower}.jpg"
            thumb_path = out_dir / f"{code_lower}_thumb.jpg"
            mid_path = out_dir / f"{code_lower}_mid.jpg"

            # Skip the DB read entirely when all artifacts already exist.
            if (
                out_path.exists()
                and out_path.stat().st_size > 0
                and thumb_path.exists()
                and thumb_path.stat().st_size > 0
                and mid_path.exists()
                and mid_path.stat().st_size > 0
            ):
                stats["skipped"] += 1
                stats["thumbs"] += 1
                stats["mids"] += 1
                continue

            row = conn.execute(
                """
                SELECT c.cover_b64 FROM title_covers c
                JOIN titles t ON t.id = c.title_id
                WHERE t.code = %s
                """,
                (code,),
            ).fetchone()
            b64_data = row[0] if row else None
            if not b64_data:
                stats["failed"] += 1
                continue

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
            if _ensure_mid(code_lower, b64_data):
                stats["mids"] += 1
    finally:
        conn.close()

    return stats


def main() -> int:
    stats = export_covers()
    print(
        f"Cover export complete: "
        f"total={stats['total']}, exported={stats['exported']}, "
        f"skipped={stats['skipped']}, failed={stats['failed']}, "
        f"thumbs={stats['thumbs']}, mids={stats['mids']}"
    )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
