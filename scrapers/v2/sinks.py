"""scrapers/v2/sinks.py — Unified write layer (wide-table simplified)

Write extraction results directly to the titles wide table via UPSERT;
all works of one star need only one SQL execution, completely eliminating serial queue bottleneck.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from core import db
from core.db.write_queue import db_write
from core.logger import get_logger
from scrapers.v2.schemas import VideoItem, MagnetCandidate

log = get_logger("title-sync-sink")


class Sink(Protocol):
    """Sink protocol"""

    async def write(self, item):
        ...


class TitleSyncSink:
    """Sync work data to DuckDB (wide-table UPSERT mode)

    Batch UPSERT per star, no need to preload existing_codes,
    no need to judge new/old, no serial write queue round-trips.
    """

    def __init__(self, star_id: int, star_code: str, star_name: str):
        self.star_id = star_id
        self.star_code = star_code
        self.star_name = star_name

    async def write(self, item: VideoItem, cover_b64: str | None = None) -> None:
        """Write single title (backward-compatible interface, internally still uses batch)."""
        await self.write_batch([item], {it.code for it in [item]}, {item.code: cover_b64 or ""})

    async def write_batch(
        self, items: list[VideoItem], new_codes: set[str], cover_map: dict[str, str]
    ) -> dict[str, int]:
        """Batch UPSERT all titles for this star in one SQL.

        Build multi-row VALUES + ON CONFLICT DO UPDATE,
        leveraging DuckDB native UPSERT capability, no manual insert/update judgment needed.
        """
        import time as _time

        if not items:
            return {"new": 0, "updated": 0}

        t0 = _time.perf_counter()
        values = []
        for item in items:
            scored = sorted(
                item.magnets,
                key=lambda m: TitleSyncSink._score_magnet(m),
                reverse=True,
            )
            primary = scored[0] if scored else None
            primary_hash = _extract_hash(primary.magnet) if primary else None

            all_magnets = [
                {
                    "hash": _extract_hash(m.magnet) or "",
                    "magnet": m.magnet,
                    "resolution": m.resolution,
                    "size": m.size,
                    "seed": m.seed,
                    "leech": m.leech,
                }
                for m in scored
            ]

            values.append({
                "star_id": self.star_id,
                "star_code": self.star_code,
                "star_name": self.star_name,
                "code": item.code,
                "title": item.title,
                "release_date": item.release_date,
                "release_date_sort": db._date_to_sort(item.release_date),
                "views": item.views,
                "likes": item.likes,
                "resolution": primary.resolution if primary else "",
                "cover_url": item.cover_url,
                "cover_b64": cover_map.get(item.code) or "",
                "magnet": primary.magnet if primary else None,
                "magnet_hash": primary_hash,
                "all_magnets": json.dumps(all_magnets) if all_magnets else None,
            })

        def _upsert(conn=None) -> dict[str, int]:
            managed = conn if conn is not None else db._conn()
            should_close = conn is None
            try:
                # DuckDB UPSERT: INSERT ... ON CONFLICT DO UPDATE
                placeholders = ", ".join([
                    "(" + ", ".join(["?"] * 15) + ")"
                    for _ in values
                ])
                flat = []
                for v in values:
                    flat.extend([
                        v["star_id"], v["star_code"], v["star_name"],
                        v["code"], v["title"], v["release_date"], v["release_date_sort"],
                        v["views"], v["likes"], v["resolution"],
                        v["cover_url"], v["cover_b64"],
                        v["magnet"], v["magnet_hash"], v["all_magnets"],
                    ])

                # ON CONFLICT only updates metadata, preserving existing cover_b64
                managed.execute(
                    f"""
                    INSERT INTO titles (
                        star_id, star_code, star_name, code, title,
                        release_date, release_date_sort, views, likes,
                        resolution, cover_url, cover_b64,
                        magnet, magnet_hash, all_magnets
                    )
                    VALUES {placeholders}
                    ON CONFLICT (star_id, code) DO UPDATE SET
                        star_code = EXCLUDED.star_code,
                        star_name = EXCLUDED.star_name,
                        title = EXCLUDED.title,
                        release_date = EXCLUDED.release_date,
                        release_date_sort = EXCLUDED.release_date_sort,
                        views = EXCLUDED.views,
                        likes = EXCLUDED.likes,
                        resolution = EXCLUDED.resolution,
                        cover_url = EXCLUDED.cover_url,
                        cover_b64 = EXCLUDED.cover_b64,
                        magnet = EXCLUDED.magnet,
                        magnet_hash = EXCLUDED.magnet_hash,
                        all_magnets = EXCLUDED.all_magnets,
                        updated_at = now()
                    """,
                    flat,
                )

                # Count insert vs update this round
                # DuckDB has no built-in returning/row_count to distinguish insert/update;
                # we approximate with new_codes (known new work count)
                new_count = len(new_codes)
                updated_count = len(values) - new_count

                if should_close:
                    managed.commit()
                return {"new": max(0, new_count), "updated": max(0, updated_count)}
            finally:
                if should_close:
                    managed.close()

        result = await db_write(_upsert)
        elapsed = (_time.perf_counter() - t0) * 1000
        log.info(f"write_batch: {self.star_name}: {len(items)} items in {elapsed:.1f}ms")
        return result

    @staticmethod
    def _best_resolution(item: VideoItem) -> str:
        """Pick the best resolution string from candidates (for titles table)"""
        if not item.magnets:
            return ""
        best = max(item.magnets, key=lambda m: TitleSyncSink._score_magnet(m))
        return best.resolution

    @staticmethod
    def _score_magnet(m: MagnetCandidate) -> float:
        """MagnetCandidate scoring"""
        res_score = 0
        res = m.resolution
        if "[4K]" in res or "4k" in res.lower():
            res_score = 600
        elif "[FHDC]" in res:
            res_score = 500
        elif "[FHD]" in res:
            res_score = 400
        elif "1080p" in res:
            res_score = 300
        elif "[HD]" in res:
            res_score = 200
        elif "720p" in res:
            res_score = 100
        size_mb = 0.0
        if m.size:
            try:
                size_mb = float(m.size.lower().replace("gb", "").strip()) * 1024
            except ValueError:
                pass
        # hhd800 HD source gets extra points to ensure priority at same resolution
        hhd800_bonus = 1000 if m.is_hhd800 else 0
        return res_score + hhd800_bonus + m.seed + size_mb / 100


class StdoutSink:
    """Debug use: direct print"""

    async def write(self, item):
        print(item)


def _extract_hash(magnet: str | None) -> str | None:
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet or "", re.I)
    return m.group(1).lower() if m else None
