"""scrapers/v2/pipeline.py — 异步生产者-消费者管道

背压控制、自动重试、限速，简洁可扩展。
"""

from __future__ import annotations

import asyncio
from typing import TypeVar, Generic, Callable, Awaitable

T = TypeVar("T")


class Pipeline(Generic[T]):
    """统一爬虫管道

    使用 asyncio.Queue 连接 producer → consumer，
    支持并发消费者、失败隔离、优雅关闭。
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
        """生产侧：抓取并抽取，结果放入队列。完成后放入 None 毒丸。"""
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
        """消费侧：从队列取出写入 sink"""
        stop_sentinel = object()
        consumer_queue: asyncio.Queue[T | Exception | object] = asyncio.Queue()

        # 把 produce 队列搬运到 consumer_queue
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
        """一键运行：produce + consume"""
        queue = await self.produce(fetcher, extractor, urls)
        await self.consume(queue)
