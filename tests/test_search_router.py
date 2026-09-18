"""tests/test_search_router.py — /api/search regression tests

Covers the ijavtorrent catalog search endpoint: collection-consistent
filtering (multi-star / no-magnet dropped), code-exact relevance,
in-library and followed enrichment.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import search as search_router
from backend.routers.auth import require_auth


def _card(code: str, actresses: list[tuple[str, str]], magnet: bool = True) -> str:
    """Minimal ijavtorrent .video-item card. actresses = [(name, slug-id)]."""
    movie = f'<a href="/movie/{code.lower()}-12345"><img alt="{code} sample title"/></a>'
    stars_html = "".join(f'<a href="/actress/{slug}">{name}</a>' for name, slug in actresses)
    magnet_row = ""
    if magnet:
        magnet_row = (
            '<tr style="vertical-align: middle"><td>'
            f'<a href="magnet:?xt=urn:btih:{"a" * 40}&dn={code}">dl</a>'
            '<i class="fa-weight-hanging"></i> 5.2 GB <strong>S:</strong> 10'
            "</td></tr>"
        )
    return (
        f'<div class="video-item">{movie}'
        '<div class="mb-2">Released 01/15/2026</div>'
        f'<div class="mb-1">{stars_html}</div><table>{magnet_row}</table></div>'
    )


_SEARCH_PAGE = (
    "<html><body>"
    # Kept: solo work, in the test DB, actress already followed
    + _card("SOLO-001", [("Test Star", "test-star-1")])
    # Kept: solo work, not in DB, actress not followed
    + _card("SOLO-002", [("New Star", "new-star-2")])
    # Dropped: multi-star (star_count > 1)
    + _card("ORGY-003", [("Test Star", "test-star-1"), ("New Star", "new-star-2")])
    # Dropped: no magnet
    + _card("NOMAG-004", [("New Star", "new-star-2")], magnet=False)
    + "</body></html>"
)

_FAKE_CONFIG = {
    "stars": [
        {
            "name": "Test Star",
            "jp": "Test Star",
            "handle": "test_star",
            "code": "SOLO-001",
            "type": "solo",
            "star_page_url": "https://ijavtorrent.com/actress/test-star-1",
        }
    ]
}


@pytest.fixture(autouse=True)
def _clear_cache():
    search_router._items_cache.clear()
    yield
    search_router._items_cache.clear()


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Remote fetch → sample page
    monkeypatch.setattr(
        "scrapers.v2.fetchers.HttpxFetcher.fetch",
        AsyncMock(return_value=_SEARCH_PAGE),
    )
    # Config → one followed star
    monkeypatch.setattr(search_router, "_load_config", lambda: _FAKE_CONFIG)
    # DB → temp file with SOLO-001 in titles
    db_file = tmp_path / "t.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute("CREATE TABLE titles (code VARCHAR)")
    conn.execute("INSERT INTO titles VALUES ('SOLO-001')")
    conn.close()
    monkeypatch.setattr(
        search_router, "_db_conn", lambda *a, **kw: duckdb.connect(str(db_file))
    )

    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: None
    app.include_router(search_router.router)
    return TestClient(app)


def test_search_filters_and_enriches(client):
    res = client.get("/api/search", params={"q": "sample title"})
    assert res.status_code == 200
    data = res.json()

    # Multi-star and no-magnet cards are dropped (same rules as sync)
    assert [it["code"] for it in data["items"]] == ["SOLO-001", "SOLO-002"]

    solo1, solo2 = data["items"]
    assert solo1["in_library"] is True
    assert solo1["stars"] == [
        {"name": "Test Star", "url": "https://ijavtorrent.com/actress/test-star-1", "followed": True}
    ]
    assert solo2["in_library"] is False
    assert solo2["stars"][0]["followed"] is False
    assert solo1["size"] == "5.2 GB"
    assert solo1["seeds"] == 10
    assert solo1["release_date"] == "01/15/2026"


def test_search_code_query_keeps_exact_match_only(client):
    """ijav search is fuzzy full-text; a code-like query filters to the exact code."""
    res = client.get("/api/search", params={"q": "solo-002"})
    assert res.status_code == 200
    assert [it["code"] for it in res.json()["items"]] == ["SOLO-002"]


def test_search_rejects_short_query(client):
    assert client.get("/api/search", params={"q": "x"}).status_code == 422


def test_search_fetch_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(
        "scrapers.v2.fetchers.HttpxFetcher.fetch",
        AsyncMock(side_effect=TimeoutError("ijav down")),
    )
    res = client.get("/api/search", params={"q": "anything"})
    assert res.status_code == 502
