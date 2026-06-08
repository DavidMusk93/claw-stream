# HTTPS Architecture and Maintenance Guide

## 1. Architecture

```
User Browser
     │
     │ HTTPS :443
     v
┌─────────────────────────────┐
│  Caddy (reverse proxy)      │
│  - Auto TLS (Let's Encrypt) │
│  - Certificate auto-renewal │
│  - Access logs              │
└─────────────────────────────┘
     │
     │  HTTP :3000 / :8765
     v
┌──────────────┐    ┌──────────────┐
│  Frontend    │    │  Backend     │
│  (:3000)     │    │  (:8765)     │
└──────────────┘    └──────────────┘
```

---

## 2. Components

### 2.1 Caddy

| Property | Value |
|----------|-------|
| Version | v2.11.2 (static binary) |
| Binary | `/usr/local/bin/caddy` |
| Config | `/root/claw-stream/Caddyfile` |
| systemd unit | `caddy-claw` |
| Data directory | `/root/.local/share/caddy/` (certificates, ACME account) |

**Caddyfile:**

```caddyfile
rn.guohuasun.com {
    reverse_proxy localhost:3000
    log {
        output file /root/claw-stream/logs/caddy-access.log {
            roll_size 10MB
            roll_keep 5
        }
    }
}

:80 {
    respond /health "OK" 200
}
```

- `rn.guohuasun.com` listens on 443 and serves HTTPS by default.
- `:80` is required for Let's Encrypt HTTP-01 challenge validation and must remain open.

---

## 3. Port Allocation

| Port | Protocol | Service | Externally Accessible | Notes |
|------|----------|---------|----------------------|-------|
| 80 | TCP | Caddy | Yes | Let's Encrypt validation; do not block |
| 443 | TCP | Caddy | Yes | **HTTPS entrypoint** |
| 8765 | TCP | FastAPI backend | No | localhost + Caddy only |
| 3000 | TCP | Nuxt frontend | No | localhost + Caddy only |

---

## 4. Certificate Management

### 4.1 Auto-provisioning and Renewal

Caddy manages certificates automatically:

1. **First start**: Caddy requests a certificate from Let's Encrypt.
2. **Renewal**: Caddy renews the certificate before expiry (checks within the default 60-day window).
3. **Storage**: `/root/.local/share/caddy/certificates/`

### 4.2 Force Re-issue

```bash
systemctl stop caddy-claw
rm -rf /root/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/rn.guohuasun.com
systemctl start caddy-claw
```

---

## 5. Operations

```bash
# Check service status
systemctl status caddy-claw

# Restart
systemctl restart caddy-claw

# Validate config and hot-reload
caddy reload --config /root/claw-stream/Caddyfile

# View access logs
tail -f /root/claw-stream/logs/caddy-access.log

# Test HTTPS
curl -s https://rn.guohuasun.com/ | head
```

---

## 6. Troubleshooting

### 6.1 Browser Certificate Error

1. Check certificate expiry: `openssl s_client -connect rn.guohuasun.com:443`
2. Check Caddy status: `systemctl status caddy-claw`
3. Verify port 80 is open: `curl -I http://rn.guohuasun.com/.well-known/acme-challenge/test`

### 6.2 HTTPS Works but Page is Blank / 404

1. Check backend: `curl http://localhost:8765/api/health`
2. Check frontend: `curl http://localhost:3000/`
3. Check reverse proxy config: `grep reverse_proxy /root/claw-stream/Caddyfile`

See also [Tracing and Logging](tracing-logging.md) for log analysis.
