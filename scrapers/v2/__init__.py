"""scrapers/v2 — 声明式管道爬虫架构

核心设计：
- Schema-first: Pydantic 模型定义数据结构
- Fetcher: 统一获取层 (httpx / playwright)
- Extractor: 统一抽取层 (selectolax CSS)
- Sink: 统一写入层 (DuckDB)
- Pipeline: 异步生产者-消费者管道
"""

from __future__ import annotations
