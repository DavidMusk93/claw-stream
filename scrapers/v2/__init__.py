"""scrapers/v2 — Declarative pipeline crawler architecture

Core design:
- Schema-first: Pydantic models define data structures
- Fetcher: Unified fetch layer (httpx / playwright)
- Extractor: Unified extraction layer (selectolax CSS)
- Sink: Unified write layer (DuckDB)
- Pipeline: Async producer-consumer pipeline
"""

from __future__ import annotations
