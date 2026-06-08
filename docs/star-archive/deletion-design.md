# Actor Deletion Design — Safe and Robust Data and Cache Cleanup

## 1. Problem Background

Deleting an actor (star) triggers changes across multiple data layers:

| Layer | Data | Location |
|---|---|---|
| Config | Star entries in `config.json` | File system |
| Database | `stars` / `titles` / `social_posts` | `data/claw.duckdb` |
| Cache | Downloaded torrent files (video + `.torrent`) | `cache/torrent/<hash>/` |
| Memory | Handles / trackers in `TorrentEngine.torrents` | Process memory |

**Historical bug**: The `delete_star` endpoint originally called `db.delete_star_by_code()` first, then queried `titles.magnet_hash` to clean up cache. Because the records were already deleted, `SELECT ... WHERE star_code = ?` always returned empty, leaving torrent files on disk as **orphan torrents**.

## 2. Safe Deletion Flow

The correct ordering guarantees that resource identifiers are collected **before** persistent data is removed:

1. Collect identifiers (magnet hashes, video paths)
2. Delete persistent data (`config.json`, database records)
3. Release external resources (disk cache, memory handles)

### 2.1 Flow Diagram

```
User calls DELETE /api/stars/{code}
        │
        ▼
┌─────────────────────────┐
│ 1. Remove star entry    │
│    from config.json     │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 2. Query database for   │
│    all magnet hashes    │
│    belonging to star    │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 3. Delete database      │
│    records (stars /     │
│    titles / social_posts│
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 4. Call engine.         │
│    remove_torrent()     │
│    for each hash        │
└─────────────────────────┘
        │
        ▼
┌─────────────────────────┐
│ 5. Invalidate stars     │
│    response cache       │
└─────────────────────────┘
```

### 2.2 Key Code Structure

```python
@router.delete("/{code}")
async def delete_star(code: str, request: Request) -> dict[str, Any]:
    # 1. Config layer removal
    config = _load_config()
    original_len = len(config.get("stars", []))
    config["stars"] = [s for s in config["stars"] if s.get("code") != code]
    if len(config["stars"]) == original_len:
        raise HTTPException(status_code=404, detail="Actor not found")
    _save_config(config)

    # 2. Collect magnet hashes BEFORE database deletion
    engine = request.app.state.engine
    magnet_hashes: list[str] = []
    try:
        conn = duckdb.connect(DB_PATH)
        try:
            rows = conn.execute("""
                SELECT magnet_hash
                FROM titles
                WHERE star_code = ? AND magnet_hash IS NOT NULL
            """, [code]).fetchall()
            magnet_hashes = [h for (h,) in rows if h]
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"delete_star: failed to query magnet hashes for {code}: {e}")

    # 3. Database layer deletion
    from core import db
    deleted = db.delete_star_by_code(code)
    if not deleted:
        log.warning(f"star {code} removed from config but not found in db")

    # 4. Cache layer deletion (per-torrent try/except)
    for hash_str in magnet_hashes:
        try:
            await asyncio.to_thread(engine.remove_torrent, hash_str)
        except Exception as e:
            log.warning(f"delete_star: failed to remove torrent {hash_str[:12]}...: {e}")

    # 5. Cache invalidation
    invalidate_stars_cache()
    return {"code": code, "deleted": True}
```

## 3. Fault Tolerance and Idempotency

### 3.1 Per-Torrent Failure Isolation

`engine.remove_torrent()` may fail due to file locks or abnormal libtorrent handle states. The design requirements are:

- Wrap each call in its own `try/except` so one failure does not block cleanup of other torrents
- Log a warning for manual investigation
- Do **not** roll back database deletion because the actor record should still be removed

### 3.2 Repeated Deletion Safety

| Operation | Idempotency Guarantee |
|---|---|
| `engine.remove_torrent()` | Returns `False` if torrent is absent; does not raise |
| `db.delete_star_by_code()` | Uses `DELETE ... WHERE id = ?`; repeated execution is harmless |
| `config.json` filtering | List comprehension naturally deduplicates |

### 3.3 Database Transaction Boundary

`delete_star_by_code` executes all deletions inside a single connection:

```sql
DELETE FROM social_posts WHERE star_id = ?;
DELETE FROM titles WHERE star_id = ?;
DELETE FROM stars WHERE id = ?;
COMMIT;
```

The three statements are atomic. If the transaction fails, the database remains consistent and `delete_star` can be retried.

## 4. Orphan Torrent GC

Even with a correct deletion flow, orphan torrents can still appear:

- Backend crash during deletion
- A single `remove_torrent()` failure that is not retried
- Legacy bugs (e.g., the historical `IPVR-317` case)

### 4.1 Detection Logic

`TorrentEngine.gc_orphaned_torrents()` scans `cache/torrent/` and compares directory names against `titles.magnet_hash` in the database:

```python
def gc_orphaned_torrents(self, db_path: str) -> int:
    disk_hashes = {
        name for name in os.listdir(self.cache_dir)
        if len(name) == 40 and os.path.isdir(os.path.join(self.cache_dir, name))
    }
    conn = duckdb.connect(db_path)
    try:
        db_hashes = {
            h for (h,) in conn.execute(
                "SELECT DISTINCT magnet_hash FROM titles WHERE magnet_hash IS NOT NULL"
            ).fetchall() if h
        }
    finally:
        conn.close()

    orphaned = sorted(disk_hashes - db_hashes)
    removed = 0
    for hash_str in orphaned:
        # If loaded in engine, use standard removal to release handles;
        # otherwise delete the directory directly.
        ...
    return removed
```

### 4.2 Cleanup Strategy

1. If the torrent is loaded in `TorrentEngine`, call `remove_torrent(hash)` to release libtorrent file descriptors
2. If not loaded, delete `cache/torrent/<hash>/` directly
3. Log the count of cleaned directories

### 4.3 Relationship with `_enforce_cache_limit`

`TorrentEngine._enforce_cache_limit()` evicts torrents when cache exceeds the threshold, but it only iterates over `engine.torrents`. **Orphan torrents are not in `engine.torrents`, so cache eviction never cleans them.** This is why an independent GC mechanism is required.

## 5. Implementation Checklist

| Item | Status | Notes |
|---|---|---|
| Fix `delete_star` ordering bug | ✅ Done | Query magnet hashes before DB deletion |
| Startup orphan scan | ✅ Done | `_cleanup_orphaned()` runs at engine startup |
| Manual GC API | ✅ Done | `POST /api/cache/gc-orphans` triggers `gc_orphaned_torrents()` |
| Orphan count in metrics | ❌ Not implemented | `/api/cache/metrics` does not report orphans yet |
| Deletion audit log | ❌ Not implemented | No structured log of who deleted which actor |

## 6. Related Files

| File | Responsibility |
|---|---|
| [`backend/routers/stars.py`](../../backend/routers/stars.py) | `delete_star` endpoint |
| [`core/db/crud.py`](../../core/db/crud.py) | `delete_star_by_code` transaction |
| [`backend/services/torrent_engine.py`](../../backend/services/torrent_engine.py) | `remove_torrent`, `gc_orphaned_torrents`, `_enforce_cache_limit` |
| [`backend/routers/cache.py`](../../backend/routers/cache.py) | `POST /api/cache/gc-orphans` and metrics endpoints |

See also:
- [Tiered Cache](tiered-cache.md) — eviction policy details
- [Cache Architecture](cache-architecture.md) — cache lifecycle and best practices
