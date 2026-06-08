# HTTPS 架构与维护指南

## 1. 当前架构

```
用户浏览器
     │
     │ HTTPS :443
     v
┌─────────────────────────────┐
│  Caddy (reverse proxy)      │
│  - 自动 TLS (Let's Encrypt) │
│  - 证书自动续期             │
│  - 访问日志                 │
└─────────────────────────────┘
     │
     │  HTTP :3000 / :8765
     v
┌──────────────┐    ┌──────────────┐
│  Frontend    │    │  Backend     │
│  (:3000)     │    │  (:8765)     │
└──────────────┘    └──────────────┘

s-ui (:8444) ──→ 独立运行，与 Web 服务无交集
```

**历史：为什么曾经走 8443？**

最初 443 端口被 `s-ui`（代理面板）占用，Caddy 无法监听 443，因此 HTTPS 临时部署在 **8443**。后续已将 s-ui 的 443 入站迁移到 **8444**，Caddy 接管 443。

---

## 2. 组件说明

### 2.1 Caddy

- **版本**: v2.11.2（静态编译二进制）
- **安装路径**: `/usr/local/bin/caddy`
- **配置路径**: `Caddyfile`
- **systemd 服务**: `caddy-claw`
- **数据目录**: `/root/.local/share/caddy/`（证书、ACME 账户）

**Caddyfile 内容：**

```
rn.guohuasun.com {
    reverse_proxy localhost:3000
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

- `rn.guohuasun.com` 默认监听 443，提供 HTTPS
- `:80` 仅用于 Let's Encrypt HTTP-01 挑战验证（必须保持开放）

---

## 3. 端口占用表

| 端口 | 协议 | 服务 | 外部可访问 | 说明 |
|------|------|------|-----------|------|
| 80 | TCP | Caddy | ✅ | Let's Encrypt 验证，不可关闭 |
| 443 | TCP | Caddy | ✅ | **HTTPS 入口** |
| 8444 | TCP | s-ui | ✅ | 代理服务（原 443，已迁移） |
| 8765 | TCP | FastAPI backend | ❌ | 仅 localhost + Caddy |
| 3000 | TCP | Nuxt frontend | ❌ | 仅 localhost + Caddy |

---

## 4. 证书管理

### 4.1 自动申请与续期

Caddy 内置证书管理，无需手动操作：
1. **首次启动**: Caddy 自动向 Let's Encrypt 申请证书
2. **续期**: Caddy 在证书到期前自动续期（默认 60 天周期内检查）
3. **存储**: `/root/.local/share/caddy/certificates/`

### 4.2 强制重新申请

```bash
systemctl stop caddy-claw
rm -rf /root/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/rn.guohuasun.com
systemctl start caddy-claw
```

---

## 5. 日常维护

```bash
# 查看服务状态
systemctl status caddy-claw

# 重启
systemctl restart caddy-claw

# 验证配置后热重载
caddy reload --config Caddyfile

# 查看访问日志
tail -f logs/caddy-access.log

# 测试 HTTPS
curl -s https://rn.guohuasun.com/ | head
```

---

## 6. 故障排查

### 6.1 浏览器报证书错误

1. 检查证书是否过期：`openssl s_client -connect rn.guohuasun.com:443`
2. 检查 Caddy 是否运行：`systemctl status caddy-claw`
3. 检查 80 端口是否开放：`curl -I http://rn.guohuasun.com/.well-known/acme-challenge/test`

### 6.2 HTTPS 通但页面空白/404

1. 检查 backend：`curl http://localhost:8765/api/health`
2. 检查 frontend：`curl http://localhost:3000/`
3. 检查 Caddy 反向代理配置：`grep reverse_proxy Caddyfile`

### 6.3 s-ui 与 Caddy 端口冲突

```bash
ss -tlnp | grep :443
lsof -i :443
```

解决：将 s-ui 的 443 入站迁移到 8444，然后重启 Caddy。
