"""scrapers/v2/sinks.py — Unified write layer (wide-table simplified)

Write extraction results directly to the titles wide table via UPSERT;
all works of one star need only one SQL execution, completely eliminating serial queue bottleneck.
Cover blobs go to the separate title_covers table (1:1 with titles).
"""

from __future__ import annotations

import re
from typing import Protocol

from psycopg.types.json import Jsonb

from core import db
from core.db.write_queue import db_write
from core.logger import get_logger
from scrapers.v2.schemas import VideoItem, MagnetCandidate

log = get_logger("title-sync-sink")

# Covers taller than wide are never legit full covers — they are vertical
# front-cover thumbnails (e.g. ijavtorrent's 300x426 / 564x800) that break
# the aspect-ratio placeholders in list rendering. Normal covers are 0.6-0.8.
COVER_MAX_HW_RATIO = 1.0


class Sink(Protocol):
    """Sink protocol"""

    async def write(self, item):
        ...


class TitleSyncSink:
    """Sync work data to PostgreSQL (wide-table UPSERT mode)

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
        leveraging PostgreSQL native UPSERT capability, no manual insert/update judgment needed.
        """
        import time as _time

        if not items:
            return {"new": 0, "updated": 0, "skipped_no_magnet": 0,
                    "skipped_bad_cover": 0, "skipped_no_cover": 0}

        t0 = _time.perf_counter()
        values = []
        skipped_no_magnet = 0
        skipped_bad_cover = 0
        skipped_no_cover = 0
        for item in items:
            # Never record titles without a usable magnet: they are unplayable
            # and only clutter the catalog. Candidates whose btih hash cannot
            # be extracted count as unusable. A source listing the code with
            # valid magnets later will insert it then.
            scored = sorted(
                (m for m in item.magnets if _extract_hash(m.magnet)),
                key=lambda m: TitleSyncSink._score_magnet(m),
                reverse=True,
            )
            if not scored:
                skipped_no_magnet += 1
                continue
            primary = scored[0]
            primary_hash = _extract_hash(primary.magnet)

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

            cover_b64 = cover_map.get(item.code) or ""
            cover_dims = db._cover_dims_from_b64(cover_b64)
            if cover_dims and cover_dims[1] / cover_dims[0] > COVER_MAX_HW_RATIO:
                # Taller-than-wide covers are vertical front-cover thumbnails,
                # not usable covers — drop the blob and treat as coverless.
                skipped_bad_cover += 1
                cover_b64 = ""
                cover_dims = None
            if not cover_b64 and item.code in new_codes:
                # New titles without a cover never enter the DB; staying
                # "new" means the next sync retries the cover download.
                skipped_no_cover += 1
                continue
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
                "resolution": primary.resolution,
                "cover_url": item.cover_url,
                "cover_b64": cover_b64,
                "cover_w": cover_dims[0] if cover_dims else None,
                "cover_h": cover_dims[1] if cover_dims else None,
                "magnet": primary.magnet,
                "magnet_hash": primary_hash,
                "all_magnets": Jsonb(all_magnets) if all_magnets else None,
            })

        def _upsert(conn=None) -> dict[str, int]:
            managed = conn if conn is not None else db._conn()
            should_close = conn is None
            try:
                # Pool connections are autocommit: wrap the multi-statement
                # upsert in an explicit transaction so rows + covers land atomically.
                with managed.transaction():
                    # INSERT ... ON CONFLICT DO UPDATE, RETURNING id+code for the
                    # cover upsert below (works for both inserted and updated rows).
                    placeholders = ", ".join([
                        "(" + ", ".join(["%s"] * 16) + ")"
                        for _ in values
                    ])
                    flat = []
                    for v in values:
                        flat.extend([
                            v["star_id"], v["star_code"], v["star_name"],
                            v["code"], v["title"], v["release_date"], v["release_date_sort"],
                            v["views"], v["likes"], v["resolution"],
                            v["cover_url"], v["cover_w"], v["cover_h"],
                            v["magnet"], v["magnet_hash"], v["all_magnets"],
                        ])

                    # ON CONFLICT only updates metadata. Covers are deliberately
                    # kept out of this statement: they live in the title_covers
                    # table, and an empty fresh cover must never wipe an existing
                    # one. Fresh covers are applied separately below, only for
                    # rows that actually have one.
                    rows = managed.execute(
                        f"""
                        INSERT INTO titles (
                            star_id, star_code, star_name, code, title,
                            release_date, release_date_sort, views, likes,
                            resolution, cover_url, cover_w, cover_h,
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
                            magnet = EXCLUDED.magnet,
                            magnet_hash = EXCLUDED.magnet_hash,
                            all_magnets = EXCLUDED.all_magnets,
                            updated_at = now()
                        RETURNING id, code
                        """,
                        flat,
                    ).fetchall()
                    id_by_code = {row[1]: row[0] for row in rows}

                    new_covers = [v for v in values if v["cover_b64"]]
                    if new_covers:
                        with managed.cursor() as cur:
                            cur.executemany(
                                """
                                INSERT INTO title_covers (title_id, cover_b64)
                                VALUES (%s, %s)
                                ON CONFLICT (title_id) DO UPDATE SET
                                    cover_b64 = EXCLUDED.cover_b64,
                                    updated_at = now()
                                """,
                                [(id_by_code[v["code"]], v["cover_b64"]) for v in new_covers],
                            )
                            cur.executemany(
                                "UPDATE titles SET cover_w = %s, cover_h = %s,"
                                " updated_at = now() WHERE id = %s",
                                [(v["cover_w"], v["cover_h"], id_by_code[v["code"]]) for v in new_covers],
                            )

                # Export fresh covers to disk (full + thumb) so the frontend
                # gets direct static URLs right away instead of going through
                # the /api/cover DB fallback until the next manual export.
                for v in values:
                    if v["cover_b64"]:
                        db._write_cover_to_disk(v["code"], v["cover_b64"])

                # Count insert vs update this round; skipped new codes (no
                # usable cover) must not inflate the new count.
                new_count = sum(1 for v in values if v["code"] in new_codes)
                updated_count = len(values) - new_count

                return {
                    "new": max(0, new_count),
                    "updated": max(0, updated_count),
                    "skipped_no_magnet": skipped_no_magnet,
                    "skipped_bad_cover": skipped_bad_cover,
                    "skipped_no_cover": skipped_no_cover,
                }
            finally:
                if should_close:
                    managed.close()

        if not values:
            log.info(
                f"write_batch: {self.star_name}: all {len(items)} items skipped"
                f" (no_magnet={skipped_no_magnet}, no_cover={skipped_no_cover})"
            )
            return {"new": 0, "updated": 0, "skipped_no_magnet": skipped_no_magnet,
                    "skipped_bad_cover": skipped_bad_cover, "skipped_no_cover": skipped_no_cover}

        result = await db_write(_upsert)
        elapsed = (_time.perf_counter() - t0) * 1000
        skipped = (
            f" (skipped: no_magnet={skipped_no_magnet},"
            f" bad_cover={skipped_bad_cover}, no_cover={skipped_no_cover})"
            if skipped_no_magnet or skipped_bad_cover or skipped_no_cover else ""
        )
        log.info(f"write_batch: {self.star_name}: {len(values)} items in {elapsed:.1f}ms{skipped}")
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
        # VR tags first: "[4KVR]" contains "4k" as a substring
        if "[8KVR]" in res:
            res_score = 700
        elif "[4KVR]" in res:
            res_score = 650
        elif "[4K]" in res or "4k" in res.lower():
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
