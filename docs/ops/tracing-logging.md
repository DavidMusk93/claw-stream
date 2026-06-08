# Tracing and Logging

## 1. Log Files

| Log | Path | Content |
|-----|------|---------|
| Backend access | `logs/backend-access.log` | HTTP method, URL, status code, latency, client IP |
| Torrent engine | `logs/torrent-engine.log` | Torrent add, priority, alert, GC |
| Video stream | `logs/video-stream.log` | Range requests, hole detection, seek |
| Piece tracker | `logs/piece-tracker.log` | Piece state changes, head_ready |
| Frontend | `logs/frontend.log` | Nuxt SSR logs |
| Caddy access | `logs/caddy-access.log` | HTTPS requests, 502 / timeout |
| systemd | `journalctl -u <unit>` | Process start / crash / restart |

---

## 2. Key Log Identifiers

| Identifier | Source | Meaning |
|------------|--------|---------|
| `bootstrap-first` | torrent-engine | Finished torrent skipped recheck |
| `cache warming retry` | torrent-engine | Re-applies priority every 10 s |
| `cache eviction triggered` | torrent-engine | Cache exceeded 95 % threshold |
| `punch hole` | torrent-engine | L3→L4 demotion, releases middle pieces |
| `play priority` | torrent-engine | Head + tail urgent download |
| `piece finished` | torrent-engine | libtorrent hash verification passed |
| `read_video_range attempt` | video-stream | Range request attempt, hole status |
| `stream_video response` | stream-router | Response status, timing |
| `GET /api/health` | backend-access | Health check |

---

## 3. Troubleshooting Decision Trees

### Symptom: No response / black screen after clicking play

```bash
# 1. Check if the head is ready
curl /api/check/<hash>
# head_ready=false → head not ready; check /torrent/status state and peers
# head_ready=true  → continue below

# 2. Test the stream directly
curl /stream/<hash> -H "Range: bytes=0-1023"
# 503 → checking_files; wait 10 s and retry
# 416 → hole; libtorrent is downloading
# 206 → server is healthy; investigate the browser

# 3. Open browser DevTools → Console
# Verify video.src and Network requests
```

### Symptom: Playback stutters / seek hangs

```bash
# 1. Inspect logs/video-stream.log
# hole=true + elapsed>=2.0s → slow download or dead torrent

# 2. Inspect logs/torrent-engine.log
# peers=0 → dead torrent; switch magnet
# download_rate=0 → no active peers

# 3. Check sparse file status
stat --format="logical=%s actual=%b*%B" /root/claw-stream/cache/torrent/<hash>/.../*.mp4
```

### Symptom: Backend 502

```bash
# 1. Check Caddy for 502 or timeout
journalctl -u caddy-claw | grep -E "502|timeout"

# 2. Check frontend errors
journalctl -u star-archive-frontend | grep -E "error|warn"

# 3. Verify Nuxt has not crashed and restarted repeatedly
```

See [Timeout Debug](../analysis/timeout-debug.md) for upstream timeout analysis and [Safari code=4](../analysis/safari-code4.md) for media errors.

---

## 4. Common Commands

```bash
# Tail backend logs in real time
journalctl -u star-archive-backend -f

# Query torrent status
curl -s http://localhost:8765/torrent/status/<hash> | python3 -m json.tool

# Check cache size
du -sh /root/claw-stream/cache/torrent/* 2>/dev/null | sort -rh | head -10

# Test a stream range
curl -s --range "bytes=0-1023" http://localhost:8765/stream/<hash> | wc -c

# Check libtorrent version
python3 -c "import libtorrent; print(libtorrent.version)"
```
