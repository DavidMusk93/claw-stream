"""core/db/write_queue.py — 进程内 DuckDB 串行写队列

所有写操作通过单一 worker 协程串行执行，worker 持有持久 DuckDB 连接，
彻底消除每次操作都新建/关闭连接的开销。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from core.logger import get_logger

log = get_logger("db-write-queue")


class DuckDBWriteQueue:
    """串行化同一进程内的所有 DuckDB 写操作。

    使用 asyncio.Queue + 单 worker 协程，所有写请求排队执行。
    worker 持有单一持久连接，避免每次操作新建/关闭连接。
    通过 Future 将结果返回给调用方。
    """

    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None
        self._conn = None
        self._maxsize = maxsize
        self._started = False

    def start(self) -> None:
        """在事件循环中启动 worker（幂等）"""
        if self._started:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        # 创建持久连接
        from core.db.connection import _conn
        self._conn = _conn()
        self._worker_task = loop.create_task(self._worker())
        self._started = True
        log.info("DuckDB write queue started with persistent connection")

    async def _worker(self) -> None:
        """单 worker：从队列取出并执行写操作，复用持久连接"""
        while True:
            item = await self._queue.get()
            if item is None:  # poison pill
                self._queue.task_done()
                break
            func, args, kwargs, future = item
            start = time.perf_counter()
            try:
                # 将持久连接注入函数的 conn 参数
                # 若调用方已显式传入 conn，则覆盖为队列的持久连接（统一由队列管理）
                kwargs_with_conn = {**kwargs, "conn": self._conn}
                result = func(*args, **kwargs_with_conn)
                elapsed = (time.perf_counter() - start) * 1000
                log.debug(f"{func.__name__} -> ok ({elapsed:.1f}ms)")
                future.set_result(result)
            except Exception as exc:
                elapsed = (time.perf_counter() - start) * 1000
                log.error(f"{func.__name__} -> {type(exc).__name__}: {exc} ({elapsed:.1f}ms)")
                future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def enqueue(self, func: Callable, *args, **kwargs) -> Any:
        """将写操作入队，等待执行完成后返回结果"""
        self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        # 记录排队等待时间
        enqueue_ts = time.perf_counter()
        await self._queue.put((func, args, kwargs, future))
        result = await future
        wait_ms = (time.perf_counter() - enqueue_ts) * 1000
        if wait_ms > 100:
            log.warning(f"{func.__name__} total wait={wait_ms:.1f}ms (queue depth={self._queue.qsize()})")
        return result

    async def stop(self) -> None:
        """优雅关闭"""
        if self._worker_task and not self._worker_task.done():
            await self._queue.put(None)
            await self._worker_task
            self._started = False
        if self._conn:
            self._conn.close()
            self._conn = None
            log.info("DuckDB write queue stopped, connection closed")


# 全局单例（进程级）
_default_queue = DuckDBWriteQueue()


def get_queue() -> DuckDBWriteQueue:
    return _default_queue


async def db_write(func: Callable, *args, **kwargs) -> Any:
    """快捷函数：将同步写操作入队并等待结果"""
    return await _default_queue.enqueue(func, *args, **kwargs)
