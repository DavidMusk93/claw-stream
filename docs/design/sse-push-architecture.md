# SSE Push Architecture — Eliminating Frontend Polling

> First principle: **A low-end device should run a smooth frontend experience.**
>
> Every HTTP request costs battery, CPU, and bandwidth. On a cheap Android phone
> or an old laptop, 4 parallel polling loops are not "acceptable overhead" —
> they are the reason the UI stutters.

---

## 1. The Problem with Polling

Before SSE, the frontend ran four independent short-polling loops:

| Loop | Interval | Endpoint | Trigger condition |
|------|----------|----------|-------------------|
| Sync status | 2s | `GET /api/stars/sync` | User clicks Refresh |
| Add-star wait | 3s | `GET /api/stars` | After adding a new actor |
| Video player | 2s | `GET /torrent/status/{hash}` | While video modal is open |
| Cache panel | 5s | `GET /api/cache` | While cache panel is open |

### Why polling violates our first principle

1. **Wasted energy** — 2-second polling means ~30 HTTP requests per minute even when
   *nothing has changed*. On a low-end phone, waking the radio 30×/min drains battery
   and heats the device.

2. **Stale data by design** — A torrent piece finishes at T+0ms. The frontend learns
   about it at T+2000ms (best case). On a 3G connection, this becomes T+4000ms.
   The loading spinner spins for seconds after the data is already ready.

3. **Connection overhead** — Each poll = TCP handshake (or TLS re-negotiation) +
   HTTP headers + JSON parse. Four loops × 30 req/min = 120 unnecessary round-trips.

4. **Head-of-line blocking** — On slow networks, the 2s polling interval becomes
   a queue of backlogged requests. The browser's 6-connection limit gets consumed
   by status polls instead of fetching covers or video segments.

---

## 2. The SSE Solution

**One persistent HTTP connection. Server pushes only when state changes.**

```
┌─────────────┐            ┌──────────────┐
│   Browser   │◄──────────►│  FastAPI     │
│  (1x SSE)   │   events   │  /api/events │
└─────────────┘            └──────┬───────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
               sync.py    torrent_engine.py   cache.py
```

### Event types

| Event | Source | Payload | Replaces |
|-------|--------|---------|----------|
| `sync.started` | `sync.py` | `{started_at}` | Manual syncRunning=true |
| `sync.completed` | `sync.py` | `{log_lines, total_new, failed, elapsed}` | 5s sync-status polling |
| `sync.error` | `sync.py` | `{error, elapsed}` | 5s sync-status polling |
| `sync.resync_required` | `EventBus` | `{}` | Slow-client safety net (see §4) |
| `star.ready` | `stars.py` | `{code, name, titles_count}` | 3s add-star polling |
| `torrent.head_ready` | `TorrentEngine` | `{hash}` | video status polling |
| `torrent.status` | `TorrentEngine` | `{hash, state}` | video status polling |
| `torrent.progress` | `TorrentEngine` | `{hash, state, progress, download_rate, upload_rate, peers, ready, head_ready, video_size, local_size, verified_pieces}` | 5s video status polling |
| `cache.update` | `TorrentEngine` / `cache.py` | `{action, hash}` | 30s cache polling |

### `torrent.progress`: throttled snapshot push

State *transitions* are rare, but progress/speed during an active download is a
continuous signal. Instead of letting the frontend poll for it, the engine
pushes a throttled snapshot every **2 seconds** from the alert thread
(`TorrentEngine._maybe_push_progress`), only for torrents that are downloading,
checking, or were played within the last 10 minutes.

The payload is built from **in-memory state only** (`handle.status()` +
`PieceStateTracker` counters — no disk I/O), and carries every field the player
UI renders. The frontend merges it into local state directly and never refetches
`/torrent/status` on a schedule; `local_size` is a verified-bytes estimate
(exact on-disk size still comes from the one-shot status fetch at subscribe time
and from resync).

### The key insight: state change is rare

A sync runs once every few hours. A torrent's `head_ready` flips exactly once.
Cache updates happen during active downloads, but still far less than 5×/second.

**Polling sends N requests for M state changes (N ≫ M).**
**SSE sends exactly M events.**

---

## 3. Cross-Thread Publishing (The Hard Part)

libtorrent runs its alert loop in a daemon thread. Python's `asyncio` event loop
lives in the main thread. Publishing from the alert thread requires bridging them:

```python
# torrent_engine.py

class TorrentEngine:
    def __init__(self, ...):
        # Capture main event loop at construction time
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None

    def _emit_event(self, event: str, data: dict) -> None:
        if self._main_loop:
            asyncio.run_coroutine_threadsafe(
                publish_event(event, data),
                self._main_loop
            )
```

`run_coroutine_threadsafe()` is thread-safe and non-blocking. The alert thread
fires-and-forgets; the main loop schedules the coroutine without blocking
torrent I/O.

---

## 4. Resilience: Reconnect + Resync, No Polling Fallback

SSE connections can drop (network switch, sleep/wake, proxy timeout) and client
event queues can overflow on slow devices. We handle both **without any
polling fallback**:

| Failure | Mechanism |
|---------|-----------|
| Connection drop | Server sends `retry: 3000` as the first frame; browser `EventSource` reconnects natively (frontend keeps an exponential-backoff guard) |
| Slow client (queue full, 256 events) | `EventBus` **coalesces**: it drains the client queue and enqueues one `sync.resync_required` marker instead of silently disconnecting. The frontend refetches its state once and continues |
| Missed one-shot event (`torrent.head_ready`) | Covered by the same `sync.resync_required` refetch plus the `torrent.progress` stream, which re-asserts `head_ready=true` every 2s while the torrent is active |

**Why no polling fallback at all?** A fallback timer masks event-loss bugs and
re-creates the battery/CPU cost SSE was meant to remove. Resync-on-demand is
cheaper and safer than trying to deliver every intermediate frame to a slow
client. The frontend contains **zero `setInterval` status loops**; the only
`setTimeout`s left are UI interaction timers (control-bar hide, gesture hints).

---

## 5. Performance Impact

### Before (polling)

```
4 loops × 30 req/min = 120 HTTP requests/minute
Per request: ~800 bytes headers + TLS overhead
Monthly baseline (24×7): ~5.1 GB of status polls
```

### After (pure SSE, no polling)

```
1 SSE connection (persistent, ~50 bytes heartbeat/30s + retry: 3000 hint)
torrent.progress: 0.5 events/s × ~200B, only while a torrent is active
Fallback polls: 0
Monthly baseline: ~0.1 GB
Reduction: ~98%
```

### Latency improvement

| Scenario | Polling latency | SSE latency |
|----------|----------------|-------------|
| Sync completes | 0–2000ms | ~0ms |
| Piece finishes → head_ready | 0–2000ms | ~0ms |
| Cache item deleted | 0–5000ms | ~0ms |
| New star titles ready | 0–3000ms | ~0ms |

---

## 6. Connection to First Principles

> **"低配置也要跑出流畅前端体验"**
> *(Low-end hardware must run a smooth frontend experience.)*

### 6.1 Battery life is user experience

A $80 Android phone has a 3000mAh battery. 30 HTTP requests/minute keeping the
radio awake consumes ~15% extra battery per hour. The user blames the *app*,
not the phone. SSE reduces radio wake-ups by ~90%.

### 6.2 Perceived speed > actual speed

Users do not measure milliseconds. They measure *"how long did the spinner spin?"*
With polling, the spinner spins for 2 extra seconds after the work is done.
With SSE, the spinner disappears the instant the server finishes. The app *feels*
10× faster even if the backend processing time is identical.

### 6.3 Network is the bottleneck on low-end devices

A cheap phone on 3G has:
- High RTT (200–500ms)
- Limited concurrent connections (6 per origin)
- Frequent packet loss

Polling amplifies all three problems. SSE compresses 120 connections/minute
into 1 persistent connection.

### 6.4 The 1% rule

If 1% of users are on low-end devices, and we make the app tolerable for them,
we make it *blazing* for the other 99%. SSE improves latency for everyone,
but the improvement is life-or-death for low-end users.

---

## 7. Implementation Checklist

- [x] `core/events.py` — Async pub/sub event bus (singleton) + slow-client coalesce/resync
- [x] `backend/routers/events.py` — `/api/events` SSE endpoint with heartbeat + `retry: 3000`
- [x] `sync.py` — Broadcast `sync.started/completed/error`
- [x] `stars.py` — Broadcast `star.ready` after `_bg_sync`
- [x] `torrent_engine.py` — `_emit_event()` + cross-thread `run_coroutine_threadsafe`
- [x] `torrent_engine.py` — Broadcast `torrent.head_ready` on piece finish
- [x] `torrent_engine.py` — Broadcast `torrent.status` on finished/checked
- [x] `torrent_engine.py` — Broadcast `torrent.progress` (2s throttle, in-memory only)
- [x] `torrent_engine.py` — Broadcast `cache.update` on add/remove/periodic_clean
- [x] `cache.py` — Broadcast `cache.update` on delete/gc
- [x] `frontend/composables/useEventSource.ts` — Global SSE manager with auto-reconnect
- [x] `frontend/pages/index.vue` — Listen to `sync.*` + `star.ready` + resync (no polling)
- [x] `frontend/composables/useVideoPlayer.ts` — Pure SSE status (progress merge + event-driven head-ready wait)
- [x] `frontend/components/cache/CachePanel.vue` — `cache.update` debounce + `torrent.progress` field-level updates (no polling)

---

## 8. Future Work

- **WebSocket bidirectional**: If we need client→server commands (e.g., seek
  without HTTP POST), upgrade SSE to WebSocket. SSE is strictly server→client,
  which matches our current push-only needs.
- **Event persistence**: Replay missed events for reconnecting clients.
  Currently a reconnect starts fresh; for critical events like `sync.completed`,
  a short replay buffer would improve reliability.
- **Selective subscription**: Filter events by hash or star code so the server
  only pushes relevant data, reducing SSE bandwidth for users with many stars.
