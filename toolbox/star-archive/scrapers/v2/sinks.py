"""scrapers/v2/sinks.py — 统一写入层

将抽取结果写入 DuckDB，所有写操作通过全局串行队列执行，
彻底消除并发锁冲突。
"""

from __future__ import annotations

from typing import Protocol

from core import db
from core.db.write_queue import db_write
from scrapers.v2.schemas import VideoItem, MagnetCandidate


class Sink(Protocol):
    """写入器协议"""

    async def write(self, item):
        ...


class TitleSyncSink:
    """同步作品数据到 DuckDB

    处理 insert / update 逻辑，保留已有 cover，管理 magnet 优先级。
    所有 DB 写操作通过全局串行队列执行。
    """

    def __init__(self, star_id: int, name: str):
        self.star_id = star_id
        self.name = name

    async def write(self, item: VideoItem, cover_b64: str | None = None, is_new: bool | None = None) -> None:
        """写入单个 title。

        cover_b64: 新作品预下载的封面，None 表示更新场景（保留已有 cover）。
        is_new: 若传入则跳过数据库存在性检查，直接按指定类型处理。
        """
        code = item.code
        views = item.views
        likes = item.likes

        if is_new is None:
            exists = await db_write(db.title_exists, self.star_id, code)
        else:
            exists = not is_new
        if exists:
            # 更新元数据，保留已有 cover
            conn = db._conn()
            row = conn.execute(
                "SELECT id, cover_b64 FROM titles WHERE star_id = ? AND code = ?",
                (self.star_id, code),
            ).fetchone()
            title_id, existing_cover = row if row else (None, None)
            conn.close()

            await db_write(
                db.upsert_title,
                star_id=self.star_id,
                code=code,
                title=item.title,
                release_date=item.release_date,
                views=views,
                likes=likes,
                resolution=self._best_resolution(item),
                download_url="",
                cover_url=item.cover_url,
                cover_b64=cover_b64 if cover_b64 is not None else existing_cover,
            )
            # 存储所有 magnet：按评分排序，最佳高清源设为 primary
            scored = sorted(item.magnets, key=lambda m: TitleSyncSink._score_magnet(m), reverse=True)
            for idx, m in enumerate(scored):
                if m.magnet:
                    await db_write(db.upsert_magnet, title_id, m.magnet, is_primary=(idx == 0))
        else:
            # 新 title：使用预下载的封面（若无则空）
            title_id = await db_write(
                db.upsert_title,
                star_id=self.star_id,
                code=code,
                title=item.title,
                release_date=item.release_date,
                views=views,
                likes=likes,
                resolution=self._best_resolution(item),
                download_url="",
                cover_url=item.cover_url,
                cover_b64=cover_b64 or "",
            )
            # 存储所有 magnet：按评分排序，最佳高清源设为 primary
            scored = sorted(item.magnets, key=lambda m: TitleSyncSink._score_magnet(m), reverse=True)
            for idx, m in enumerate(scored):
                if m.magnet:
                    await db_write(db.upsert_magnet, title_id, m.magnet, is_primary=(idx == 0))

    async def write_batch(self, items: list[VideoItem], new_codes: set[str], cover_map: dict[str, str]) -> dict[str, int]:
        """批量写入该 star 的所有 titles 和 magnets，只进行一次 db_write 队列调用。

        相比逐个 write()，批量模式将每个 star 的 N 次数据库往返压缩为 1 次，
        彻底解决串行写队列在大批量同步时的性能瓶颈。
        """
        star_id = self.star_id

        def _batch() -> dict[str, int]:
            import re
            conn = db._conn()
            try:
                # 预加载该 star 的所有现有 title id + cover
                existing_rows = conn.execute(
                    "SELECT id, code, cover_b64 FROM titles WHERE star_id = ?",
                    (star_id,),
                ).fetchall()
                existing: dict[str, tuple[int, str | None]] = {
                    code: (tid, cover) for tid, code, cover in existing_rows
                }

                total_new = 0
                total_updated = 0

                for item in items:
                    code = item.code
                    is_new = code in new_codes
                    b64 = cover_map.get(code)

                    # 评分 magnets
                    if item.magnets:
                        scored = sorted(
                            item.magnets,
                            key=lambda m: TitleSyncSink._score_magnet(m),
                            reverse=True,
                        )
                        best_resolution = scored[0].resolution if scored else ""
                    else:
                        scored = []
                        best_resolution = ""

                    release_date_sort = db._date_to_sort(item.release_date)

                    if not is_new:
                        row = existing.get(code)
                        if row:
                            title_id, existing_cover = row
                        else:
                            title_id = None
                            existing_cover = None

                        if title_id is None:
                            # 数据库中不存在（可能之前被清理），降级为插入
                            conn.execute(
                                """
                                INSERT INTO titles (star_id, code, title, release_date, release_date_sort,
                                                    views, likes, resolution, download_url, cover_url,
                                                    cover_b64, cover_path)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    star_id, code, item.title, item.release_date,
                                    release_date_sort, item.views, item.likes,
                                    best_resolution, "", item.cover_url,
                                    b64 or "", "",
                                ),
                            )
                            title_id = conn.execute(
                                "SELECT id FROM titles WHERE star_id = ? AND code = ?",
                                (star_id, code),
                            ).fetchone()[0]
                            total_new += 1
                        else:
                            cover_to_use = b64 if b64 is not None else existing_cover
                            conn.execute(
                                """
                                UPDATE titles SET
                                    title = ?,
                                    release_date = ?,
                                    release_date_sort = ?,
                                    views = ?,
                                    likes = ?,
                                    resolution = ?,
                                    download_url = ?,
                                    cover_url = ?,
                                    cover_b64 = ?,
                                    cover_path = ?,
                                    updated_at = now()
                                WHERE id = ?
                                """,
                                (
                                    item.title, item.release_date, release_date_sort,
                                    item.views, item.likes, best_resolution, "",
                                    item.cover_url, cover_to_use, "", title_id,
                                ),
                            )
                            total_updated += 1

                        # upsert magnets（复用连接）
                        for idx, m in enumerate(scored):
                            if m.magnet:
                                h = re.search(r"xt=urn:btih:([a-f0-9]{40})", m.magnet, re.I)
                                hash_str = h.group(1).lower() if h else None
                                if hash_str:
                                    row = conn.execute(
                                        "SELECT 1 FROM magnets WHERE title_id = ? AND hash = ?",
                                        (title_id, hash_str),
                                    ).fetchone()
                                    if row:
                                        conn.execute(
                                            """
                                            UPDATE magnets SET magnet = ?, is_primary = ?
                                            WHERE title_id = ? AND hash = ?
                                            """,
                                            (m.magnet, idx == 0, title_id, hash_str),
                                        )
                                    else:
                                        conn.execute(
                                            """
                                            INSERT INTO magnets (title_id, magnet, hash, is_primary)
                                            VALUES (?, ?, ?, ?)
                                            """,
                                            (title_id, m.magnet, hash_str, idx == 0),
                                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO titles (star_id, code, title, release_date, release_date_sort,
                                                views, likes, resolution, download_url, cover_url,
                                                cover_b64, cover_path)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                star_id, code, item.title, item.release_date,
                                release_date_sort, item.views, item.likes,
                                best_resolution, "", item.cover_url,
                                b64 or "", "",
                            ),
                        )
                        title_id = conn.execute(
                            "SELECT id FROM titles WHERE star_id = ? AND code = ?",
                            (star_id, code),
                        ).fetchone()[0]
                        total_new += 1

                        for idx, m in enumerate(scored):
                            if m.magnet:
                                h = re.search(r"xt=urn:btih:([a-f0-9]{40})", m.magnet, re.I)
                                hash_str = h.group(1).lower() if h else None
                                if hash_str:
                                    conn.execute(
                                        """
                                        INSERT INTO magnets (title_id, magnet, hash, is_primary)
                                        VALUES (?, ?, ?, ?)
                                        """,
                                        (title_id, m.magnet, hash_str, idx == 0),
                                    )

                conn.commit()
                return {"new": total_new, "updated": total_updated}
            finally:
                conn.close()

        return await db_write(_batch)

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
        hhd800_bonus = 200 if "hhd800" in m.magnet.lower() else 0
        return res_score + hhd800_bonus + m.seed + size_mb / 100


class StdoutSink:
    """调试用：直接打印"""

    async def write(self, item):
        print(item)
