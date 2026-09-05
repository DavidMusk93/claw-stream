"""scripts/fill_cover_dims.py — Backfill titles.cover_w/cover_h from disk images.

The frontend reserves each cover's box via aspect-ratio before the bytes
arrive; without real dimensions it guessed 2:3 portrait while virtually all
covers are ~3:2 landscape, so every load caused a layout jump.

Reads images/titles/{code}/{code}.jpg headers with PIL (fast, no decode of
pixel data) and updates matching title rows.

Usage:
    .venv/bin/python scripts/fill_cover_dims.py           # dry-run report
    .venv/bin/python scripts/fill_cover_dims.py --apply   # write dims

--apply opens the DB read-write: stop star-archive-backend first (DuckDB
allows a single writer process).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = Path("data/claw.duckdb")
IMAGES_DIR = Path("images/titles")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write dims (stop the backend first)")
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

    conn = duckdb.connect(str(DB_PATH), read_only=not args.apply)
    try:
        # Point queries against a table with ~8GB of blob columns otherwise
        # pull entire row groups into the buffer manager and OOM this 4GB box.
        conn.execute("SET memory_limit = '1GB'")
        conn.execute("SET temp_directory = '/tmp/duckdb-spill'")
        rows = conn.execute(
            "SELECT id, code FROM titles WHERE cover_w IS NULL OR cover_h IS NULL"
        ).fetchall()
        todo = [(r[0], dims[r[1].upper()]) for r in rows if r[1].upper() in dims]

        # Rows without a disk cover: fall back to decoding the DB blob.
        # NEVER bulk-select cover_b64 (the DB holds ~8GB of blobs — OOM on
        # this 4GB box); stream id list first, then fetch one blob at a time.
        import base64
        import io
        filled_ids = {t[0] for t in todo}
        blob_ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM titles "
                "WHERE (cover_w IS NULL OR cover_h IS NULL) AND cover_b64 IS NOT NULL"
            ).fetchall()
        ]
        for _id in blob_ids:
            if _id in filled_ids:
                continue
            b64 = conn.execute(
                "SELECT cover_b64 FROM titles WHERE id = ?", [_id]
            ).fetchone()[0]
            try:
                data = b64.split(",", 1)[1] if b64.startswith("data:image/") else b64
                with Image.open(io.BytesIO(base64.b64decode(data))) as img:
                    todo.append((_id, (img.width, img.height)))
            except Exception:
                continue

        print(f"disk covers: {len(dims)}, rows missing dims: {len(rows)}, fillable: {len(todo)}")
        if args.apply and todo:
            conn.executemany(
                "UPDATE titles SET cover_w = ?, cover_h = ? WHERE id = ?",
                [(w, h, _id) for _id, (w, h) in todo],
            )
            conn.commit()
            print(f"updated {len(todo)} rows")
        elif not args.apply:
            print("dry-run; pass --apply to write")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
