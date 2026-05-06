# HTTPS 架构与维护指南

## 1. 当前架构

```
用户浏览器
     |
     |  HTTPS :443
     v
+-----------------------------+
|  Caddy (reverse proxy)      |
|  - 自动 TLS (Let's Encrypt) |
|  - 证书自动续期              |
|  - 访问日志                  |
+-----------------------------+
     |
     |  HTTP :8765
     v
+-----------------------------+
|  cache-server.py            |
|  - libtorrent 引擎           |
|  - 视频流 /stream/<hash>     |
|  - API /api/*                |
+-----------------------------+

s-ui (:8444) ──→ 独立运行，与 Web 服务无交集
```

**历史：为什么曾经走 8443？**

最初 443 端口被 `s-ui`（代理面板）占用，Caddy 无法监听 443，因此 HTTPS 临时部署在 **8443**。后续已将 s-ui 的 443 入站迁移到 **8444**，Caddy 接管 443。

---

## 2. 组件说明

### 2.1 Caddy

- **版本**: v2.11.2（静态编译二进制）
- **安装路径**: `/usr/local/bin/caddy`
- **配置路径**: `toolbox/actress-report/Caddyfile`
- **systemd 服务**: `caddy-claw`
- **数据目录**: `/root/.local/share/caddy/`（证书、ACME 账户）

**Caddyfile 内容：**

```
rn.guohuasun.com {
    reverse_proxy localhost:8765
    log {
        output file logs/caddy-access.log {
            roll_size 10MB
            roll_keep 5
        }
    }
}

:80 {
    respond /health "OK" 200
}
```

- `rn.guohuasun.com` 默认监听 443，提供 HTTPS，反向代理到 `localhost:8765`
- `:80` 仅用于 Let's Encrypt HTTP-01 挑战验证（必须保持开放）

### 2.2 cache-server.py

- **监听**: `0.0.0.0:8765`
- **启动方式**: `nohup python3 cache-server.py --port 8765 &`
- **注意**: 代码更新后**必须手动重启进程**

### 2.3 s-ui (:443)

- **用途**: 代理流量（VLESS/Xray 等）
- **TLS 证书**: `images.apple.com`（伪装）
- **与 Web 服务关系**: 无直接交互，仅端口冲突

---

## 3. 端口占用表

| 端口 | 协议 | 服务 | 外部可访问 | 说明 |
|------|------|------|-----------|------|
| 80 | TCP | Caddy | ✅ | Let's Encrypt 验证，不可关闭 |
| 443 | TCP | Caddy | ✅ | **HTTPS 入口** |
| 8444 | TCP | s-ui | ✅ | 代理服务（原 443，已迁移） |
| 8765 | TCP | cache-server | ✅ (本地) | 仅 localhost + Caddy 访问即可 |

---

## 4. 证书管理

### 4.1 自动申请与续期

Caddy 内置证书管理，无需手动操作：

1. **首次启动**: Caddy 自动向 Let's Encrypt 申请证书（HTTP-01 挑战 via :80）
2. **续期**: Caddy 在证书到期前自动续期（默认 60 天周期内检查）
3. **存储**: 证书保存在 `/root/.local/share/caddy/certificates/`

### 4.2 手动检查证书状态

```bash
# 查看证书信息
echo | openssl s_client -connect rn.guohuasun.com:8443 -servername rn.guohuasun.com 2>/dev/null | openssl x509 -noout -subject -dates

# 查看 Caddy 管理的所有证书
curl -s localhost:2019/config/apps/tls/certificates | python3 -m json.tool
```

### 4.3 强制重新申请

如需强制刷新证书（如域名变更）：

```bash
# 停止 Caddy
systemctl stop caddy-claw

# 删除旧证书
rm -rf /root/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/rn.guohuasun.com

# 重新启动
systemctl start caddy-claw
```

---

## 5. 日常维护

### 5.1 查看服务状态

```bash
# Caddy
systemctl status caddy-claw

# cache-server
ps aux | grep cache-server | grep -v grep
curl -s http://localhost:8765/api/metrics | python3 -m json.tool

# 端口监听
ss -tlnp | grep -E ":80|:443|:8443|:8765"
```

### 5.2 重启服务

**修改 cache-server.py 后必须重启：**

```bash
pkill -f "cache-server.py"
cd toolbox/actress-report && nohup python3 cache-server.py --port 8765 > /dev/null 2>&1 &
```

**修改 Caddyfile 后重启：**

```bash
systemctl restart caddy-claw
# 或验证配置后热重载
caddy reload --config toolbox/actress-report/Caddyfile
```

### 5.3 日志查看

```bash
# Caddy 访问日志
tail -f toolbox/actress-report/logs/caddy-access.log

# cache-server 日志
tail -f toolbox/actress-report/logs/cache-server.log

# 系统日志
journalctl -u caddy-claw -f
```

---

## 6. 故障排查

### 6.1 浏览器报证书错误

```
症状：访问 https://rn.guohuasun.com:8443/ 时浏览器拦截
```

排查：
1. 确认 URL 带 `:8443`，不是 `:443`
2. 检查证书是否过期：`openssl x509 -in <cert> -noout -dates`
3. 检查 Caddy 是否运行：`systemctl status caddy-claw`
4. 检查 80 端口是否开放（HTTP-01 需要）：`curl -I http://rn.guohuasun.com/.well-known/acme-challenge/test`

### 6.2 HTTPS 通但页面空白/404

```
症状：证书正常，但返回 404 或无法加载
```

排查：
1. cache-server 是否在运行：`curl http://localhost:8765/`
2. Caddy 反向代理配置是否正确：`grep reverse_proxy toolbox/actress-report/Caddyfile`
3. actresses-report.html 是否存在：`ls -la actresses-report.html`

### 6.3 Caddy 无法获取证书

```
症状：Caddy 日志显示 "challenge failed"
```

常见原因：
- 80 端口被防火墙拦截
- 域名未解析到当前服务器 IP
- Let's Encrypt 速率限制（同一域名 5 次/小时）

解决：
```bash
# 确认域名解析
dig +short rn.guohuasun.com A

# 确认 80 端口可从外网访问
curl -I http://rn.guohuasun.com/health

# 查看 Caddy 详细错误
journalctl -u caddy-claw --no-pager | tail -50
```

### 6.4 s-ui 与 Caddy 端口冲突

如果 s-ui 重新占用了 443，Caddy 会启动失败。检查占用者：

```bash
ss -tlnp | grep :443
lsof -i :443
```

解决：将 s-ui 的 443 入站迁移到其他端口（如 8444），然后重启 Caddy。

---

## 7. 未来升级路径

### 7.1 去掉端口号（`:8443` → `:443`）

需要在 s-ui 面板中为 443 入站配置 **fallback**，将非代理流量回落到 Caddy：

```
s-ui 443 入站
  ├── 识别为代理协议 → 正常代理处理
  └── 识别为 Web 流量 → fallback localhost:8443
```

Caddy 此时仍监听 `:8443`，但用户访问 `https://rn.guohuasun.com/`（不带端口）时，s-ui 自动将流量转给 Caddy。

**注意**: s-ui 的 TLS 终止后，fallback 到 Caddy 的是明文 HTTP。如果 s-ui 的伪装证书不影响浏览器体验（用户通过代理客户端连接），则此方案可行。

### 7.2 纯 Caddy 方案（停用 s-ui 443）

如果不再需要 s-ui 的 443 代理：

1. 修改 s-ui 配置，让其监听其他端口（如 8444）
2. Caddy 改为监听 `:443`
3. Caddy 增加 `reverse_proxy /proxy localhost:8444` 把代理流量也转回 s-ui

---

## 8. 快速参考

| 操作 | 命令 |
|------|------|
| 查看 Caddy 状态 | `systemctl status caddy-claw` |
| 重启 Caddy | `systemctl restart caddy-claw` |
| 查看 cache-server | `ps aux \| grep cache-server` |
| 重启 cache-server | `pkill -f cache-server.py && cd toolbox/actress-report && nohup python3 cache-server.py --port 8765 &` |
| 测试 HTTPS | `curl -s https://rn.guohuasun.com/ \| head` |
| 检查证书 | `echo \| openssl s_client -connect rn.guohuasun.com:443` |
| 查看访问日志 | `tail -f logs/caddy-access.log` |
