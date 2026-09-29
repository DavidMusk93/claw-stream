#!/usr/bin/env python3
"""Shared fixtures for xstream regression tests.

Provides local BitTorrent seed fixtures so regression tests run against real BT-downloaded videos,
rather than relying on external networks or synthetic files.
"""
from __future__ import annotations

import os
import pytest

from tests.local_bt_fixture import LocalSeed, download_with_engine, cleanup_cache_dir


@pytest.fixture(scope="session")
def local_seed():
    """Start local seed session, providing a real BT seed.

    Yields a LocalSeed instance with attributes:
        - hash: str
        - listen_port: int
        - ti: libtorrent.torrent_info
    """
    seed = LocalSeed()
    try:
        yield seed
    finally:
        seed.stop()


@pytest.fixture(scope="session")
def real_video_engine(local_seed, tmp_path_factory):
    """Use TorrentEngine to download real video from local seed.

    Returns (engine, video_path, hash_str).
    engine is automatically shut down and cache cleaned up at session end.
    """
    cache_dir = str(tmp_path_factory.mktemp("bt_cache"))
    hash_str = local_seed.hash
    seed_port = local_seed.listen_port

    engine, video_path = download_with_engine(cache_dir, hash_str, seed_port, timeout=60.0)

    yield engine, video_path, hash_str

    engine.shutdown()
    cleanup_cache_dir(cache_dir, hash_str)


_PG_TABLES = ("stars, titles, title_covers, social_posts, sync_runs,"
              " user_events, title_blacklist, star_rss_state")


@pytest.fixture()
def pg_test_db():
    """Point the core.db pool at the xstream_test database and reset it.

    Sets XSTREAM_PG_DSN (database name replaced with xstream_test) before the
    lazily-initialized pool is first used, so all core.db calls — including
    ones made without an explicit conn — land in the test database. Yields a
    pooled connection; truncates all tables before and after the test.

    Skips when XSTREAM_PG_DSN is unset or xstream_test is unreachable.
    """
    dsn = os.environ.get("XSTREAM_PG_DSN")
    if not dsn:
        pytest.skip("XSTREAM_PG_DSN not set — PG-backed tests need the xstream_test database")
    os.environ["XSTREAM_PG_DSN"] = dsn.rsplit("/", 1)[0] + "/xstream_test"

    from core import db

    db.close_pool()  # drop any pool pinned to a previous DSN
    try:
        db.init_schema()
    except Exception as exc:
        pytest.skip(f"xstream_test unreachable: {exc}")

    conn = db._conn()
    try:
        conn.execute(f"TRUNCATE {_PG_TABLES} RESTART IDENTITY CASCADE")
        yield conn
        conn.execute(f"TRUNCATE {_PG_TABLES} RESTART IDENTITY CASCADE")
    finally:
        conn.close()
