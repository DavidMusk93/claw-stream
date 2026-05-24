"""core/db/write_queue.py — 进程内 DuckDB 串行写队列

所有写操作通过单一 worker 协程串行执行，避免同一进程内
多个协程同时打开写连接导致的竞争，也彻底消除跨进程锁冲突。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


class DuckDBWriteQueue:
    """串行化同一进程内的所有 DuckDB 写操作。

    使用 asyncio.Queue + 单 worker 协程，所有写请求排队执行，
    通过 Future 将结果返回给调用方。
    """

    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None
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
        self._worker_task = loop.create_task(self._worker())
        self._started = True

    async def _worker(self) -> None:
        """单 worker：从队列取出并执行写操作"""
        while True:
            item = await self._queue.get()
            if item is None:  # poison pill
                self._queue.task_done()
                break
            func, args, kwargs, future = item
            try:
                result = func(*args, **kwargs)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def enqueue(self, func: Callable, *args, **kwargs) -> Any:
        """将写操作入队，等待执行完成后返回结果"""
        self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put((func, args, kwargs, future))
        return await future

    async def stop(self) -> None:
        """优雅关闭"""
        if self._worker_task and not self._worker_task.done():
            await self._queue.put(None)
            await self._worker_task
            self._started = False


# 全局单例（进程级）
_default_queue = DuckDBWriteQueue()


def get_queue() -> DuckDBWriteQueue:
    return _default_queue


async def db_write(func: Callable, *args, **kwargs) -> Any:
    """快捷函数：将同步写操作入队并等待结果"""
    return await _default_queue.enqueue(func, *args, **kwargs)
