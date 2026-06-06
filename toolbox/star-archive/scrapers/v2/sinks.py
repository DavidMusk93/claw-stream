"""scrapers/v2/sinks.py — 统一写入层（大宽表简化版）

将抽取结果通过 UPSERT 直接写入 titles 宽表，
一个 star 的所有作品只需一次 SQL 执行，彻底消除串行队列瓶颈。
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
    """写入器协议"""

    async def write(self, item):
        ...


class TitleSyncSink:
    """同步作品数据到 DuckDB（大宽表 UPSERT 模式）

    每个 star 一次批量 UPSERT，无需预加载 existing_codes，
    无需判断新/旧，无需串行写队列往返。
    """

    def __init__(self, star_id: int, star_code: str, star_name: str):
        self.star_id = star_id
        self.star_code = star_code
        self.star_name = star_name

    async def write(self, item: VideoItem, cover_b64: str | None = None) -> None:
        """写入单个 title（兼容旧接口，内部仍走 batch）。"""
        await self.write_batch([item], {it.code for it in [item]}, {item.code: cover_b64 or ""})

    async def write_batch(
        self, items: list[VideoItem], new_codes: set[str], cover_map: dict[str, str]
    ) -> dict[str, int]:
        """批量 UPSERT 该 star 的所有 titles，一次 SQL 完成。

        构造多行 VALUES + ON CONFLICT DO UPDATE，
        利用 DuckDB 原生 UPSERT 能力，无需手动判断 insert/update。
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

                # ON CONFLICT 只更新元数据，保留已有 cover_b64
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
                    """,
                    flat,
                )

                # 统计本次插入 vs 更新
                # DuckDB 没有内置 returning/row_count 区分 insert/update，
                # 我们用 new_codes 近似（已知的新作品数）
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
        """从 candidates 中挑选最佳 resolution 字符串（用于 titles 表）"""
        if not item.magnets:
            return ""
        best = max(item.magnets, key=lambda m: TitleSyncSink._score_magnet(m))
        return best.resolution

    @staticmethod
    def _score_magnet(m: MagnetCandidate) -> float:
        """MagnetCandidate 评分"""
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
        # hhd800 高清源额外加分，确保在相同分辨率下优先
        hhd800_bonus = 1000 if m.is_hhd800 else 0
        return res_score + hhd800_bonus + m.seed + size_mb / 100


class StdoutSink:
    """调试用：直接打印"""

    async def write(self, item):
        print(item)


def _extract_hash(magnet: str | None) -> str | None:
    m = re.search(r"xt=urn:btih:([a-f0-9]{40})", magnet or "", re.I)
    return m.group(1).lower() if m else None
