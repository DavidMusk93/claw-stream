# Database (PostgreSQL)

Wide-table design. Schema lives in `core/db/schema.py` — `init_schema()` is
idempotent with `ALTER TABLE ... IF NOT EXISTS` backfills. PostgreSQL 18,
databases `xstream` (production) and `xstream_test` (tests); DSN from `XSTREAM_PG_DSN`.

## Tables

- `stars` — Actor base info (`name` UNIQUE, `jp_name`, `handle`, `code`, `type`, `note`)
- `titles` — Title metadata, inlines star and magnet info: `star_id`, `star_code`, `star_name`, `code`, `title`, `release_date`, `release_date_sort`, `views`, `likes`, `resolution`, `cover_url`, `cover_path`, `cover_w`, `cover_h` (pixels, for aspect-ratio placeholders), `charming_intro`, `jable_m3u8`, `magnet`, `magnet_hash`, `all_magnets JSONB`, `user_liked INTEGER DEFAULT 0`, `magnet_status` (NULL/`ok`/`dead`), `magnet_checked_at`, `magnet_checked_hash` (magnet liveness check, see `docs/design/magnet-check.md`); `UNIQUE(star_id, code)`
- `title_covers` — Cover blobs, 1:1 with titles: `title_id PK REFERENCES titles(id) ON DELETE CASCADE`, `cover_b64 TEXT`, `updated_at`. Kept out of `titles` so hot-table metadata updates never churn multi-hundred-KB blobs.
- `social_posts` — Social platform posts (`star_id`, `platform`, `content`, `post_url`, `posted_at`)
- `sync_runs` — Sync run history (`trigger` manual/scheduled, `status`, `started_at`, `finished_at`, `total_new`, `total_updated`, `failed_count`, `error`)
- `user_events` — User behavior events (`ts`, `event`, `code`, `star_code`, `meta JSONB`)
- `title_blacklist` — Sink-rejected titles (`star_id`, `code`, `reason`, `skip_count`, `first_seen`, `last_seen`; PK `(star_id, code)`); sync filters these before the diff so rejected covers are never re-downloaded. `last_seen` older than 7 days = one retry pass
- `star_rss_state` — Per-star RSS negative cache (`star_id` PK, `empty_streak`, `last_empty_at`): after 3 consecutive syncs with zero usable sukebei results, phase B skips that star's RSS queries for 7 days (they only burn rate-limiter slots); a non-empty result resets the streak

There is **no separate `magnets` table** anymore — magnet data lives on `titles`.

## Access Rules

All DB access goes through the psycopg pool in `core/db/connection.py`
(`_conn()`, min 1 / max 10, `autocommit=True`, `close()` returns the
connection to the pool). Never open ad-hoc connections.

- Pool connections autocommit single statements; multi-statement writes must
  wrap themselves in `with conn.transaction():`.
- psycopg3 returns JSONB columns already parsed (no `json.loads`) and
  `TIMESTAMP` columns as `datetime` objects.
- Placeholders are `%s`; `Connection` has no `executemany` — use
  `conn.cursor().executemany(...)`.
- All DB writes go through `core/db/write_queue.py` (legacy serialization
  choice, see `docs/design/architecture-decisions.md`).

## CLI

```bash
python3 -m core.db            # init schema
python3 -m core.db backfill
python3 -m core.db stats
```
