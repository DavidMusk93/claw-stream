# Case Study: Page Load Failures — Caddy Upstream Timeout

## Symptoms

Users report "the page won't load." Local `curl` tests on the server return 200, but real users (iPhone + mobile network) frequently encounter load failures.

---

## Investigation (Layered)

### Layer 1: Service Health

```bash
systemctl is-active star-archive-backend star-archive-frontend caddy-claw
ss -tlnp | grep -E '3000|8765|443'
```

Result: all three services are active and listening.

**Conclusion:** this is not a process crash.

---

### Layer 2: Local Direct Access (Bypass Caddy)

```bash
curl -H "Cookie: claw_auth=ok" http://localhost:3000/     # Nuxt frontend
curl http://localhost:8765/api/health                      # FastAPI backend
```

Result: both return 200; the home-page HTML (143 KB) is complete.

**Conclusion:** the Nuxt production build and FastAPI are healthy.

---

### Layer 3: HTTPS End-to-End (Through Caddy)

```bash
curl -L -b "claw_auth=ok" https://rn.guohuasun.com/
curl https://rn.guohuasun.com/api/health
```

Result: both return 200 with normal latency (~0.1 s).

**Trap:** `curl` uses HTTP/1.1 or HTTP/2 and runs on the same machine as Caddy, so the network is ideal. **This does not represent real-user conditions.**

---

### Layer 4: Caddy Access Log Analysis (Key)

```bash
journalctl -u caddy-claw --no-pager --since="1 hour ago" | grep -E "502|aborting|timeout"
```

**71 errors** in one hour:

```
aborting with incomplete response
writing: timeout: no recent network activity
Application error 0x100 (remote)
status: 502
```

Key characteristics:
- All errors come from **real user IPs** (e.g., 221.194.171.225, 172.225.124.222).
- Protocol is `proto: HTTP/3.0`.
- Upstream is `localhost:3000`.
- Duration spans 0.1 s ~ 0.75 s.

---

### Layer 5: Nuxt Process Stability

```bash
journalctl -u star-archive-frontend --since="1 hour ago"
```

The frontend was restarted 10+ times during the hour (manual restarts during debugging). Logs show:

```
[Vue Router warn]: No match found for location with path "/_nuxt/"
```

This indicates Vue Router attempted to match `/_nuxt/` static-resource paths and failed. Although static assets still return 200, the warning reveals a boundary case between Nuxt's static-file middleware and its router.

---

## Root Cause

### Immediate Cause

Caddy's reverse proxy to `localhost:3000` experiences **upstream response timeouts**.

Nuxt SSR for the home page requires:
1. Executing `useFetch('/api/stars')` (database query + data assembly).
2. Vue component server-side rendering (13 actors × 3 titles DOM).
3. HTML serialization.

When the cache is cold or on first visit, TTFB can reach 0.5 s ~ 0.8 s. Combined with Caddy's conservative default `response_header_timeout` and HTTP/3 (QUIC) retransmission instability on mobile networks, Caddy treats the upstream as unresponsive and returns 502 or aborts the connection.

### Deep Causes

1. **Poor HTTP/3 (QUIC) compatibility on mobile networks**: iPhone + mobile environments expose edge cases in QUIC packet-loss recovery that interact badly with Caddy/Nuxt.
2. **Conservative Caddy default reverse-proxy timeouts**: Without explicit configuration, slow Nuxt SSR responses are easily misclassified as timed out.
3. **Frequent restarts during debugging**: Multiple `systemctl restart` commands create transient 502 windows while the service is restarting.

---

## Fix

### 1. Increase Caddy Reverse-Proxy Timeouts

```caddyfile
reverse_proxy localhost:3000 {
    transport http {
        dial_timeout 10s
        response_header_timeout 30s
    }
}
```

- `dial_timeout 10s`: tolerance for connecting to Nuxt.
- `response_header_timeout 30s`: maximum wait for Nuxt response headers (covers SSR render peaks).

### 2. Rebuild Frontend and Restart

```bash
cd /root/claw-stream/frontend
npx nuxt build
systemctl restart star-archive-frontend
```

Ensure `/root/claw-stream/frontend/.output` matches the current source tree and eliminates any middleware/router compilation drift.

### 3. Future Optimizations

- **Disable HTTP/3**: If mobile 502s persist, disable QUIC in Caddy (via JSON API or global options; Caddyfile has no direct directive).
- **Nuxt page caching**: Cache SSR render results for `/api/stars` with Nuxt Nitro `routeRules`.
- **Database query optimization**: Persistent DuckDB connection + in-memory caching (already implemented; see `backend/routers/stars.py`).

---

## Lessons Learned

### 1. Layered Troubleshooting

| Layer | Check | Purpose |
|-------|-------|---------|
| L1 | `systemctl` + `ss` | Rule out process / port issues |
| L2 | `localhost` curl | Rule out application issues |
| L3 | HTTPS curl | Rule out certificate / DNS issues |
| **L4** | **Caddy access log** | **Pinpoint reverse-proxy issues (key)** |
| L5 | `journalctl` application logs | Pinpoint application internals |

**Key insight:** local `curl` success does **not** imply real-user success. Reverse-proxy logs are the mirror of actual user requests.

### 2. HTTP/3 Is a Double-Edged Sword

Caddy enables HTTP/3 by default. Under ideal conditions it reduces latency. Under:
- mobile networks (high packet loss),
- cross-carrier routing,
- certain firewall environments,

QUIC connection migration and retransmission can cause **more instability**, manifesting as `timeout: no recent network activity` and `Application error 0x100`.

### 3. Hidden Cost of SSR

Nuxt SSR is not free. Every page request must:
1. Run Vue rendering on the server.
2. Wait for data fetching (database queries, API calls).
3. Serialize HTML.

TTFB variance directly affects reverse-proxy timeout decisions. Production environments must reserve sufficient upstream timeout headroom for SSR.

---

## Related Commit

- `8ad3b3f` fix(caddy): increase reverse proxy timeouts for Nuxt SSR

See also [Tracing and Logging](tracing-logging.md) for log identifiers and [HTTPS Setup](https-setup.md) for Caddy configuration.
