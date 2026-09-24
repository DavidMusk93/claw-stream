#!/usr/bin/env python3
"""MagnetChecker regression tests.

- check_one against the local BT seed -> alive
- check_one against a random hash -> dead (short timeout, no peers)
- swap_primary_magnet / update_magnet_check_* CRUD against the claw_test PG database

Uses the shared local_seed fixture; skips automatically when the local seed
cannot start (consistent with the rest of the suite). DB-backed tests skip
when CLAW_PG_DSN is unset or claw_test is unreachable.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.magnet_checker import MagnetChecker, extract_hash
from core import db
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.skipif(
    not os.environ.get("CLAW_PG_DSN"),
    reason="CLAW_PG_DSN not set — DB tests need the claw_test database",
)


@pytest.fixture()
def checker(tmp_path):
    c = MagnetChecker(str(tmp_path / "magnet-check"), timeout=10, grace_timeout=5, concurrency=4)
    yield c
    c.shutdown()


def test_check_one_alive_from_local_seed(local_seed, checker):
    magnet = f"magnet:?xt=urn:btih:{local_seed.hash}"
    result = checker.check_one(magnet, timeout=20, extra_peers=[("127.0.0.1", local_seed.listen_port)])
    assert result["alive"] is True
    assert result["peers"] >= 1


def test_check_one_dead_random_hash(checker):
    magnet = "magnet:?xt=urn:btih:" + "ab" * 20
    result = checker.check_one(magnet, timeout=3)
    assert result["alive"] is False
    # Note: peers may be > 0 on a host running other libtorrent sessions —
    # the DHT is shared and can return stray peers. Metadata never arriving
    # is the decisive dead signal.


def test_check_one_bad_magnet(checker):
    result = checker.check_one("not-a-magnet", timeout=1)
    assert result["alive"] is False
    assert result.get("error") == "bad magnet"


def test_extract_hash():
    assert extract_hash("magnet:?xt=urn:btih:" + "AB" * 20 + "&dn=x") == "ab" * 20
    assert extract_hash("") is None


@pytest.fixture()
def temp_db(pg_test_db):
    """claw_test with schema + one title (dead primary, one live candidate)."""
    conn = pg_test_db
    conn.execute("INSERT INTO stars (name) VALUES ('Test Star')")
    star_id = conn.execute("SELECT id FROM stars WHERE name = 'Test Star'").fetchone()[0]
    dead_hash = "cd" * 20
    live_hash = "ef" * 20
    conn.execute(
        """
        INSERT INTO titles (star_id, code, magnet, magnet_hash, all_magnets)
        VALUES (%s, 'TEST-001', %s, %s, %s)
        """,
        (
            star_id,
            f"magnet:?xt=urn:btih:{dead_hash}",
            dead_hash,
            Jsonb([
                {"hash": dead_hash, "magnet": f"magnet:?xt=urn:btih:{dead_hash}"},
                {"hash": live_hash, "magnet": f"magnet:?xt=urn:btih:{live_hash}"},
            ]),
        ),
    )
    return conn, star_id, dead_hash, live_hash


def test_update_magnet_check_ok(temp_db):
    conn, star_id, dead_hash, _ = temp_db
    title_id = conn.execute("SELECT id FROM titles WHERE code = 'TEST-001'").fetchone()[0]
    db.update_magnet_check_ok(title_id, dead_hash, conn=conn)
    row = conn.execute(
        "SELECT magnet_status, magnet_checked_hash FROM titles WHERE id = %s", (title_id,)
    ).fetchone()
    assert row == ("ok", dead_hash)


def test_update_magnet_check_dead(temp_db):
    conn, star_id, dead_hash, _ = temp_db
    title_id = conn.execute("SELECT id FROM titles WHERE code = 'TEST-001'").fetchone()[0]
    db.update_magnet_check_dead(title_id, dead_hash, conn=conn)
    row = conn.execute(
        "SELECT magnet_status, magnet_hash FROM titles WHERE id = %s", (title_id,)
    ).fetchone()
    assert row == ("dead", dead_hash)


def test_swap_primary_magnet(temp_db):
    conn, star_id, dead_hash, live_hash = temp_db
    title_id = conn.execute("SELECT id FROM titles WHERE code = 'TEST-001'").fetchone()[0]
    db.swap_primary_magnet(title_id, f"magnet:?xt=urn:btih:{live_hash}", live_hash, conn=conn)
    row = conn.execute(
        "SELECT magnet, magnet_hash, magnet_status, magnet_checked_hash FROM titles WHERE id = %s",
        (title_id,),
    ).fetchone()
    assert row == (f"magnet:?xt=urn:btih:{live_hash}", live_hash, "ok", live_hash)


def test_load_titles_for_magnet_check_scopes(temp_db):
    conn, star_id, dead_hash, live_hash = temp_db
    title_id = conn.execute("SELECT id FROM titles WHERE code = 'TEST-001'").fetchone()[0]

    # Never checked -> included in unchecked / changed / all
    for scope in ("unchecked", "changed", "all"):
        rows = db.load_titles_for_magnet_check(scope, conn=conn)
        assert [r["id"] for r in rows] == [title_id]
    assert db.load_titles_for_magnet_check("dead", conn=conn) == []

    # Checked ok with same hash -> excluded from unchecked / changed
    db.update_magnet_check_ok(title_id, dead_hash, conn=conn)
    assert db.load_titles_for_magnet_check("unchecked", conn=conn) == []
    assert db.load_titles_for_magnet_check("changed", conn=conn) == []
    assert len(db.load_titles_for_magnet_check("all", conn=conn)) == 1

    # Hash changed (sync promoted a new primary) -> back in unchecked / changed
    conn.execute("UPDATE titles SET magnet_hash = %s WHERE id = %s", (live_hash, title_id))
    assert len(db.load_titles_for_magnet_check("unchecked", conn=conn)) == 1
    assert len(db.load_titles_for_magnet_check("changed", conn=conn)) == 1

    # Marked dead -> in dead / changed scopes
    db.update_magnet_check_dead(title_id, live_hash, conn=conn)
    assert len(db.load_titles_for_magnet_check("dead", conn=conn)) == 1
    assert len(db.load_titles_for_magnet_check("changed", conn=conn)) == 1

    with pytest.raises(ValueError):
        db.load_titles_for_magnet_check("bogus", conn=conn)


def test_purge_flow_seals_blacklist(temp_db):
    """Unplayable titles are listed with star_id; blacklisting + deleting them
    leaves a dead_magnet entry so sync never re-adds the code."""
    conn, star_id, dead_hash, _ = temp_db
    db.update_magnet_check_dead(
        conn.execute("SELECT id FROM titles WHERE code = 'TEST-001'").fetchone()[0],
        dead_hash, conn=conn,
    )

    unplayable = db.list_unplayable_titles(conn=conn)
    assert len(unplayable) == 1
    assert unplayable[0]["code"] == "TEST-001"
    assert unplayable[0]["star_id"] == star_id

    n = db.blacklist_titles(
        [(t["star_id"], t["code"], "dead_magnet") for t in unplayable], conn=conn
    )
    assert n == 1
    db.delete_titles_by_ids([t["id"] for t in unplayable], conn=conn)

    assert conn.execute("SELECT count(*) FROM titles").fetchone()[0] == 0
    row = conn.execute(
        "SELECT reason, skip_count FROM title_blacklist WHERE star_id = %s AND code = 'TEST-001'",
        (star_id,),
    ).fetchone()
    assert row == ("dead_magnet", 1)
    # And the sync-side loader picks it up.
    assert db.load_blacklisted_codes(conn=conn) == {(star_id, "TEST-001")}
