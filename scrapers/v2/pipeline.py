"""scrapers/v2/pipeline.py — Async producer-consumer pipeline

Backpressure control, auto retry, rate limiting, concise and extensible.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar, Generic, Callable, Awaitable

T = TypeVar("T")


class Pipeline(Generic[T]):
    """Unified crawler pipeline

    Uses asyncio.Queue to connect producer → consumer,
    supporting concurrent consumers, failure isolation, and graceful shutdown.
    """

    def __init__(
        self,
        sink: Callable[[T], Awaitable[None]],
        concurrency: int = 4,
        retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self.sink = sink
        self.concurrency = concurrency
        self.retries = retries
        self.retry_delay = retry_delay
        self._errors: list[tuple[str, Exception]] = []

    @property
    def errors(self) -> list[tuple[str, Exception]]:
        return self._errors

    async def produce(
        self,
        fetcher,
        extractor,
        urls: list[str],
        semaphore: asyncio.Semaphore | None = None,
    ) -> asyncio.Queue[T | Exception | None]:
        """Producer side: fetch and extract, put results into queue. Put None poison pill when done."""
        queue: asyncio.Queue[T | Exception | None] = asyncio.Queue()
        sem = semaphore or asyncio.Semaphore(self.concurrency)

        async def _fetch_one(url: str):
            async with sem:
                for attempt in range(self.retries):
                    try:
                        html = await fetcher.fetch(url)
                        items = extractor.extract(html)
                        for item in items:
                            await queue.put(item)
                        return
                    except Exception as exc:
                        if attempt == self.retries - 1:
                            await queue.put(exc)
                            self._errors.append((url, exc))
                        else:
                            await asyncio.sleep(self.retry_delay * (attempt + 1))

        tasks = [asyncio.create_task(_fetch_one(u)) for u in urls]
        await asyncio.gather(*tasks, return_exceptions=True)
        await queue.put(None)  # poison pill
        return queue

    async def consume(self, queue: asyncio.Queue[T | Exception | None]):
        """Consumer side: dequeue and write to sink"""
        stop_sentinel = object()
        consumer_queue: asyncio.Queue[T | Exception | object] = asyncio.Queue()

        # Move produce queue to consumer_queue
        async def _pump():
            while True:
                item = await queue.get()
                queue.task_done()
                if item is None:
                    break
                await consumer_queue.put(item)

        pump_task = asyncio.create_task(_pump())

        async def _worker():
            while True:
                item = await consumer_queue.get()
                if item is stop_sentinel:
                    consumer_queue.task_done()
                    break
                if isinstance(item, Exception):
                    consumer_queue.task_done()
                    continue
                try:
                    await self.sink(item)
                except Exception as exc:
                    self._errors.append(("sink", exc))
                finally:
                    consumer_queue.task_done()

        workers = [asyncio.create_task(_worker()) for _ in range(self.concurrency)]
        await pump_task
        for _ in range(self.concurrency):
            await consumer_queue.put(stop_sentinel)
        await asyncio.gather(*workers)

    async def run(
        self,
        fetcher,
        extractor,
        urls: list[str],
    ) -> None:
        """One-click run: produce + consume"""
        queue = await self.produce(fetcher, extractor, urls)
        await self.consume(queue)
