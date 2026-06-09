"""core/db/write_queue.py — In-process DuckDB serial write queue

All write operations are serially executed by a single worker coroutine, avoiding
contention from multiple coroutines opening write connections simultaneously within the same process, and completely eliminating cross-process lock conflicts.
"""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable

from core.logger import get_logger

log = get_logger("db-write-queue")


class DuckDBWriteQueue:
    """Serialize all DuckDB write operations within the same process.

    Uses asyncio.Queue + single worker coroutine, all write requests are queued and executed,
    returning results to the caller via Future.

    Note: DuckDB single-file database can only have one write connection holding the lock at a time,
    so the worker does not maintain a persistent connection; instead each called function manages its own connection.
    Batch scenarios should reuse connections inside the function (e.g., TitleSyncSink.write_batch).
    """

    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None
        self._maxsize = maxsize
        self._started = False

    def start(self) -> None:
        """Start worker in the event loop (idempotent)"""
        if self._started:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        self._worker_task = loop.create_task(self._worker())
        self._started = True
        log.info("DuckDB write queue started")

    async def _worker(self) -> None:
        """Single worker: dequeue and execute write operations in a thread pool.

        Uses loop.run_in_executor to offload synchronous DuckDB I/O to an independent thread,
        avoiding blocking the main event loop (especially during intensive libtorrent I/O).
        """
        loop = asyncio.get_running_loop()
        while True:
            item = await self._queue.get()
            if item is None:  # poison pill
                self._queue.task_done()
                break
            func, args, kwargs, future = item
            start = time.perf_counter()
            try:
                if kwargs:
                    bound_func = functools.partial(func, **kwargs)
                    result = await loop.run_in_executor(None, bound_func, *args)
                else:
                    result = await loop.run_in_executor(None, func, *args)
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
        """Enqueue a write operation and return the result after execution completes"""
        self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        # Record queue waiting time
        enqueue_ts = time.perf_counter()
        await self._queue.put((func, args, kwargs, future))
        result = await future
        wait_ms = (time.perf_counter() - enqueue_ts) * 1000
        if wait_ms > 100:
            log.warning(f"{func.__name__} total wait={wait_ms:.1f}ms (queue depth={self._queue.qsize()})")
        return result

    async def stop(self) -> None:
        """Graceful shutdown"""
        if self._worker_task and not self._worker_task.done():
            await self._queue.put(None)
            await self._worker_task
            self._started = False
        log.info("DuckDB write queue stopped")


# Global singleton (process-level)
_default_queue = DuckDBWriteQueue()


def get_queue() -> DuckDBWriteQueue:
    return _default_queue


async def db_write(func: Callable, *args, **kwargs) -> Any:
    """Convenience function: enqueue a synchronous write operation and wait for the result"""
    return await _default_queue.enqueue(func, *args, **kwargs)
