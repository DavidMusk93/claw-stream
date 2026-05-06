# claw-stream

> 个人作品追踪 + BitTorrent 边下边播播放器

## 项目特色

### 1. 一体化数据流
从作品发布网站自动抓取最新作品（封面、磁力链接、分辨率），按女演员聚合，生成可浏览的个人主页式报告。

### 2. BitTorrent 边下边播
基于 **libtorrent** 实现的缓存流服务器：
- **moov 头部优先下载**：分析 MP4 box 结构，优先下载 `moov` atom 所在区域，实现秒开播放
- **稀疏文件空洞检测**：Linux 稀疏文件未下载区域返回全 0，16KB 块级检测避免浏览器解析死锁
- **Range 请求 + Seek 加速**：HTTP 206 支持，Seek 时实时提升对应 pieces 优先级
- **预缓存策略**：页面加载后自动 prefetch 最新 13 部作品的前 2% 数据

### 3. 播放器体验
- 通用 Magnet 播放器：粘贴任意磁力链即可播放
- 缓冲进度实时显示（速度 / 已缓存 / 百分比）
- 弹出式视频 Modal，ESC 退出

### 4. 视觉与交互
- 阳光/暗色双主题，localStorage 持久化
- Netflix 式卡片流 + Hero Banner
-  actress 横向 Carousel 导航栏
- 一键刷新 🔄，自动重新抓取并重排

### 5. 可观测性
- 统一日志汇聚：所有组件日志写入 `logs/`，10MB 滚动 + 5 份备份自动回收
- 浏览器全局错误上报 (`window.onerror` → `/api/log`)
- `/api/metrics` 实时暴露 torrent 数量、缓存占用、完成率

### 6. 安全
- `/stream` 入口密码认证，动态密码 `rnYYmmdd{day%2}`
- 24h Cookie 会话，防君子不防小人
- `/api/regenerate` 仅允许本地/私有 IP

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  用户浏览器                                                  │
│  - 主题切换 / 搜索 / 播放器 / 缓存管理                        │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTPS :443
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  Caddy                                                      │
│  - 自动 TLS (Let's Encrypt)                                 │
│  - 反向代理 → localhost:8765                                │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTP :8765
                        ▼
┌─────────────────────────────────────────────────────────────┐
│  cache-server.py                                            │
│  ├─ TorrentEngine (libtorrent)                              │
│  │   ├─ 稀疏文件读取 + moov 检测                            │
│  │   ├─ piece 优先级控制 (urgent / sequential)              │
│  │   └─ 预缓存 vs 播放模式                                  │
│  ├─ HTTP API                                                │
│  │   ├─ /stream/<hash>   视频流 (Range 206)                 │
│  │   ├─ /torrent/add     添加 magnet                        │
│  │   ├─ /torrent/status  进度查询                           │
│  │   ├─ /api/check      头部就绪检查                        │
│  │   ├─ /api/regenerate 一键刷新                            │
│  │   ├─ /api/logs        日志浏览                           │
│  │   ├─ /api/metrics     运行指标                           │
│  │   └─ /api/log         前端错误上报                       │
│  └─ /stream & /          密码认证入口                       │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  cache/torrent/   ┌─────────┐     ┌─────────┐
  <hash>/          │ijavtorrent│     │jable.tv │
  (稀疏文件)       └────┬────┘     └────┬────┘
                        │               │
                        ▼               ▼
                   search-news.py   fetch-jable.py
                        │               │
                        └───────┬───────┘
                                ▼
                           DuckDB (data/claw.duckdb)
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
              db.py export_to_tmp    generate-report.js
              /tmp JSON (bridge)     actresses-report.html
```

### 数据流

```
config.json ──→ search-news.py ──→ DuckDB
       │
       └──────→ fetch-jable.py ──→ DuckDB
       │
       └──────→ db.py export_to_tmp ──→ /tmp JSON (bridge)
       │
       └──────→ generate-report.js ──→ actresses-report.html

refresh.sh (一键串联以上步骤)
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vanilla JS, CSS Variables, 无框架 |
| 后端 | Python 3.11, libtorrent 2.0.8 |
| 代理 | Caddy v2.11 (自动 HTTPS) |
| 数据 | DuckDB |
| 抓取 | Playwright, httpx |

## 快速开始

```bash
cd toolbox/actress-report

# 1. 启动缓存服务器
python3 cache-server.py --port 8765

# 2. 启动 Caddy (systemd 已配置)
systemctl start caddy-claw

# 3. 一键刷新数据
./refresh.sh

# 4. 浏览器访问
# https://your-domain.com/stream
# 密码: rn + 年月日 + 日期奇偶(0/1)
```

## 目录结构

```
toolbox/actress-report/
├── cache-server.py        # libtorrent + HTTP 服务器
├── generate-report.js     # HTML 报告生成器
├── search-news.py         # ijavtorrent 抓取
├── fetch-jable.py         # jable.tv 抓取
├── db.py                  # DuckDB 持久化层
├── refresh.sh             # 一键刷新脚本
├── logger.py              # 统一日志模块
├── Caddyfile              # HTTPS 反向代理配置
├── config.json            # 女演员列表 + 标题
└── logs/                  # 汇聚日志 (gitignored)
```

## 未来演进点

### 近期（1-2 周）
- [x] **DuckDB 持久化**：已完成。作品数据存入 DuckDB，增量抓取，避免重复下载封面
- [x] **缓存回收策略**：已完成。LRU 自动清理，torrent 缓存硬上限 20GB
- [ ] **定时自动刷新**：cron 或 systemd timer 每晚自动执行 `refresh.sh`
- [ ] **移动端适配**：Carousel 横向滚动在手机上体验差，需优化触控和布局
- [ ] **播放器增强**：倍速播放、键盘快捷键（空格暂停、方向键快进）

### 中期（1-2 月）
- [ ] **多分辨率选择**：同作品多个清晰度 magnet，用户可选 720p/1080p/4K
- [ ] **搜索增强**：按番号、日期范围、分辨率过滤；DuckDB/FTS 本地索引
- [ ] **用户偏好持久化**：收藏 actress、观看历史、默认排序方式
- [ ] **WebSocket 推送**：torrent 下载进度实时推送到前端，替代轮询

### 远期（3-6 月）
- [ ] **PWA 离线访问**：Service Worker 缓存静态资源，支持离线浏览已抓取数据
- [ ] **多端同步**：DuckDB → PostgreSQL 或同步到 NAS，多设备共享进度
- [ ] **AI 封面生成/修复**：缺失封面时自动从 DMM/JavBus 等多源补全
- [ ] **订阅制 RSS**：自动监控 actress 新作品，发布即推送到前端

## 相关文档

- `docs/playback-flow.md` — 播放流程技术详解
- `docs/tracing-logging.md` — 日志体系与故障排查
- `docs/https-setup.md` — HTTPS 架构与维护
- `docs/cache-server.md` — 缓存服务器 API 参考
- `docs/seek-support.md` — Seek 支持技术细节
