# 案例：网页无法打开 —— Caddy 反向代理 upstream 超时定位

## 问题现象

用户反馈"网页无法打开"。服务器端本地 curl 测试全部 200，但真实用户（iPhone + 移动网络）频繁遇到加载失败。

## 排查过程（分层递进）

### 第一层：服务存活检查

```bash
systemctl is-active star-archive-backend star-archive-frontend caddy-claw
ss -tlnp | grep -E '3000|8765|443'
```

结果：三个服务均 active，端口正常监听。

**结论**：服务没挂，不是进程崩溃问题。

---

### 第二层：本地直接访问（绕过 Caddy）

```bash
curl -H "Cookie: claw_auth=ok" http://localhost:3000/     # Nuxt 前端
curl http://localhost:8765/api/health                      # FastAPI 后端
```

结果：全部 200，首页 HTML 143KB 内容完整。

**结论**：Nuxt 生产构建和 FastAPI 本身无故障。

---

### 第三层：HTTPS 端到端测试（经 Caddy）

```bash
curl -L -b "claw_auth=ok" https://rn.guohuasun.com/
curl https://rn.guohuasun.com/api/health
```

结果：全部 200，响应时间正常（0.1s 左右）。

**陷阱**：curl 使用的是 HTTP/1.1 或 HTTP/2，且服务器与 Caddy 在同一台机器，网络环境理想。**不能代表真实用户场景**。

---

### 第四层：Caddy access log 分析（关键）

```bash
journalctl -u caddy-claw --no-pager --since="1 hour ago" | grep -E "502|aborting|timeout"
```

发现 **1 小时内 71 个错误**：

```
aborting with incomplete response
writing: timeout: no recent network activity
Application error 0x100 (remote)
status: 502
```

**关键特征**：
- 所有出错请求都来自**真实用户 IP**（221.194.171.225、172.225.124.222 等）
- 协议均为 `proto: HTTP/3.0`
- upstream 指向 `localhost:3000`
- duration 分布在 0.1s ~ 0.75s 之间

---

### 第五层：Nuxt 进程稳定性检查

```bash
journalctl -u star-archive-frontend --since="1 hour ago"
```

发现 1 小时内前端被重启了 10+ 次（调试过程中的手动重启），且日志中出现：

```
[Vue Router warn]: No match found for location with path "/_nuxt/"
```

这说明 Nuxt 生产服务器在处理 `/_nuxt/` 静态资源路径时，Vue Router 尝试匹配但失败。虽然静态资源本身返回 200，但此 warn 提示 Nuxt 的静态文件中间件和路由处理存在边界情况。

---

## 根因分析

### 直接原因

Caddy 反向代理到 `localhost:3000` 时，**upstream 响应超时**。

Nuxt SSR 渲染首页需要：
1. 执行 `useFetch('/api/stars')`（数据库查询 + 数据组装）
2. Vue 组件服务器端渲染（13 个 star × 3 个 title 的 DOM）
3. HTML 序列化

在缓存未命中或首次访问时，TTFB 可达 0.5s ~ 0.8s。加上 Caddy 默认的 `response_header_timeout` 较短，以及 HTTP/3 (QUIC) 在移动网络下的不稳定重传，导致 Caddy 认为 upstream 无响应，返回 502 或中断连接。

### 深层原因

1. **HTTP/3 (QUIC) 兼容性差**：iPhone + 移动网络环境下，QUIC 丢包重传机制与 Caddy/Nuxt 的交互存在边界 case
2. **Caddy 默认反向代理超时偏保守**：未显式配置时，Nuxt SSR 的慢响应容易被判定为超时
3. **调试过程中的频繁重启**：多次 `systemctl restart` 导致服务在重启窗口期内出现瞬时的 502

---

## 修复方案

### 1. Caddy 增加反向代理超时

```caddyfile
reverse_proxy localhost:3000 {
    transport http {
        dial_timeout 10s
        response_header_timeout 30s
    }
}
```

- `dial_timeout 10s`：连接 Nuxt 的容忍时间
- `response_header_timeout 30s`：等待 Nuxt 返回响应头的最大时间（覆盖 SSR 渲染峰谷）

### 2. 重新构建前端并重启服务

```bash
cd frontend
npx nuxt build
systemctl restart star-archive-frontend
```

确保 `.output` 产物与当前源码一致，消除中间件/路由的编译漂移。

### 3. 后续可考虑的优化

- **禁用 HTTP/3**：若移动端 502 持续出现，可在 Caddy 全局配置中关闭 QUIC（Caddy v2 需通过 JSON API 或全局 options 配置，Caddyfile 中无直接指令）
- **Nuxt 缓存层**：对 `/api/stars` 的 SSR 渲染结果做页面级缓存（如 Nuxt Nitro `routeRules`），避免每次请求都重新渲染
- **数据库查询优化**：持续化 DuckDB 连接 + 内存缓存（已实现，见 `stars.py`）

---

## 经验教训

### 1. 分层排查法

| 层级 | 检查项 | 目的 |
|------|--------|------|
| L1 | systemctl + ss | 排除进程/端口问题 |
| L2 | localhost curl | 排除应用本身问题 |
| L3 | HTTPS curl | 排除证书/DNS问题 |
| **L4** | **Caddy access log** | **定位反向代理层问题（关键）** |
| L5 | journalctl 应用日志 | 定位应用内部错误 |

**关键洞察**：curl 本地正常 ≠ 真实用户正常。反向代理层（Caddy）的日志才是真实用户请求的镜子。

### 2. HTTP/3 是双刃剑

Caddy 默认启用 HTTP/3，在理想网络下能降低延迟。但在：
- 移动网络（高丢包）
- 跨运营商
- 某些防火墙环境

下，QUIC 的连接迁移和重传机制可能导致**更多不稳定**，表现为：`timeout: no recent network activity`、`Application error 0x100`。

### 3. SSR 的隐性成本

Nuxt SSR 不是免费的。每个页面请求都要：
1. 在服务器端跑 Vue 渲染
2. 等待数据获取（数据库查询、API 调用）
3. 序列化 HTML

TTFB 的波动会直接影响反向代理的超时判定。生产环境必须为 SSR 预留足够的 upstream timeout。

---

## 相关提交

- `8ad3b3f` fix(caddy): increase reverse proxy timeouts for Nuxt SSR
