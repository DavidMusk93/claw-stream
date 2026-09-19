"""scripts/fill_cover_dims.py — Backfill titles.cover_w/cover_h from disk images.

The frontend reserves each cover's box via aspect-ratio before the bytes
arrive; without real dimensions it guessed 2:3 portrait while virtually all
covers are ~3:2 landscape, so every load caused a layout jump.

Reads images/titles/{code}/{code}.jpg headers with PIL (fast, no decode of
pixel data) and updates matching title rows.

Usage:
    .venv/bin/python scripts/fill_cover_dims.py           # dry-run report
    .venv/bin/python scripts/fill_cover_dims.py --apply   # write dims
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import _conn

IMAGES_DIR = Path("images/titles")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write dims")
    args = ap.parse_args()

    dims: dict[str, tuple[int, int]] = {}
    for code_dir in IMAGES_DIR.iterdir():
        jpg = code_dir / f"{code_dir.name}.jpg"
        if not jpg.is_file():
            continue
        try:
            with Image.open(jpg) as img:
                dims[code_dir.name.upper()] = (img.width, img.height)
        except Exception:
            continue

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, code FROM titles WHERE cover_w IS NULL OR cover_h IS NULL"
        ).fetchall()
        todo = [(r[0], dims[r[1].upper()]) for r in rows if r[1].upper() in dims]

        # Rows without a disk cover: fall back to decoding the title_covers
        # blob, fetched one row at a time to bound memory.
        import base64
        import io
        filled_ids = {t[0] for t in todo}
        blob_ids = [
            r[0] for r in conn.execute(
                "SELECT t.id FROM titles t "
                "JOIN title_covers c ON c.title_id = t.id "
                "WHERE (t.cover_w IS NULL OR t.cover_h IS NULL) AND c.cover_b64 IS NOT NULL"
            ).fetchall()
        ]
        for _id in blob_ids:
            if _id in filled_ids:
                continue
            b64 = conn.execute(
                "SELECT cover_b64 FROM title_covers WHERE title_id = %s", [_id]
            ).fetchone()[0]
            try:
                data = b64.split(",", 1)[1] if b64.startswith("data:image/") else b64
                with Image.open(io.BytesIO(base64.b64decode(data))) as img:
                    todo.append((_id, (img.width, img.height)))
            except Exception:
                continue

        print(f"disk covers: {len(dims)}, rows missing dims: {len(rows)}, fillable: {len(todo)}")
        if args.apply and todo:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE titles SET cover_w = %s, cover_h = %s WHERE id = %s",
                    [(w, h, _id) for _id, (w, h) in todo],
                )
            print(f"updated {len(todo)} rows")
        elif not args.apply:
            print("dry-run; pass --apply to write")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
