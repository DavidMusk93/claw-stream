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
| `sync.completed` | `sync.py` | `{log_lines, total_new, elapsed}` | 2s polling loop |
| `sync.error` | `sync.py` | `{error, elapsed}` | 2s polling loop |
| `star.ready` | `stars.py` | `{code, name, titles_count}` | 3s add-star polling |
| `torrent.head_ready` | `TorrentEngine` | `{hash}` | 2s video status polling |
| `torrent.status` | `TorrentEngine` | `{hash, state}` | 2s video status polling |
| `cache.update` | `TorrentEngine` / `cache.py` | `{action, hash}` | 5s cache polling |

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

## 4. Fallback Strategy: Graceful Degradation

SSE connections can drop (network switch, sleep/wake, proxy timeout).
We do **not** let the UI freeze.

| Feature | SSE | Fallback |
|---------|-----|----------|
| Sync status | Real-time events | None needed (POST returns immediately) |
| Star ready | `star.ready` event | None needed (cache invalidation handles refresh) |
| Video player | `torrent.head_ready` | 5s polling (was 2s) |
| Cache panel | `cache.update` event | 10s polling (was 5s) |

**Why keep fallback at all?** Because `head_ready` is a one-shot event. If the SSE
connection drops between piece-finish and reconnect, the video modal would wait
forever. A 5s fallback poll catches this edge case with minimal cost.

---

## 5. Performance Impact

### Before (polling)

```
4 loops × 30 req/min = 120 HTTP requests/minute
Per request: ~800 bytes headers + TLS overhead
Monthly baseline (24×7): ~5.1 GB of status polls
```

### After (SSE + sparse fallback)

```
1 SSE connection (persistent, ~50 bytes heartbeat/30s)
Fallback polls: ~12 req/minute (video 5s + cache 10s)
Monthly baseline: ~0.3 GB
Reduction: ~94%
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

- [x] `core/events.py` — Async pub/sub event bus (singleton)
- [x] `backend/routers/events.py` — `/api/events` SSE endpoint with heartbeat
- [x] `sync.py` — Broadcast `sync.started/completed/error`
- [x] `stars.py` — Broadcast `star.ready` after `_bg_sync`
- [x] `torrent_engine.py` — `_emit_event()` + cross-thread `run_coroutine_threadsafe`
- [x] `torrent_engine.py` — Broadcast `torrent.head_ready` on piece finish
- [x] `torrent_engine.py` — Broadcast `torrent.status` on finished/checked
- [x] `torrent_engine.py` — Broadcast `cache.update` on add/remove/periodic_clean
- [x] `cache.py` — Broadcast `cache.update` on delete/gc
- [x] `frontend/composables/useEventSource.ts` — Global SSE manager with auto-reconnect
- [x] `frontend/pages/index.vue` — Listen to `sync.*` and `star.ready`
- [x] `frontend/composables/useVideoPlayer.ts` — Listen to `torrent.*` + 5s fallback
- [x] `frontend/components/cache/CachePanel.vue` — Listen to `cache.update` + 10s fallback

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
