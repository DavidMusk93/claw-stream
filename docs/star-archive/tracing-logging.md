# 日志体系与故障排查

## 1. 日志文件

| 日志 | 路径 | 内容 |
|------|------|------|
| 后端访问日志 | `logs/backend-access.log` | HTTP 方法、URL、状态码、耗时、IP |
| Torrent 引擎 | `logs/torrent-engine.log` | torrent 添加、priority、alert、GC |
| 视频流 | `logs/video-stream.log` | Range 请求、hole 检测、seek |
| Piece 追踪 | `logs/piece-tracker.log` | piece 状态变化、head_ready |
| 前端 | `logs/frontend.log` | Nuxt SSR 日志 |
| Caddy 访问 | `logs/caddy-access.log` | HTTPS 请求、502/timeout |
| systemd | `journalctl -u <unit>` | 进程启动/崩溃/重启 |

---

## 2. 关键日志标识

| 标识 | 来源 | 含义 |
|------|------|------|
| `bootstrap-first` | torrent-engine | finished torrent 跳过 recheck |
| `cache warming retry` | torrent-engine | 10s 间隔重新 apply priority |
| `cache eviction triggered` | torrent-engine | 缓存超过 95% 阈值 |
| `punch hole` | torrent-engine | L3→L4 降级，释放中间 pieces |
| `play priority` | torrent-engine | head+tail urgent 下载 |
| `piece finished` | torrent-engine | libtorrent hash 校验通过 |
| `read_video_range attempt` | video-stream | Range 请求尝试、hole 状态 |
| `stream_video response` | stream-router | 响应状态、timing |
| `GET /api/health` | backend-access | health check |

---

## 3. 排查决策树

### 症状：点击播放后无反应 / 黑屏

```
1. curl /api/check/<hash> → head_ready?
   false → 头部未就绪，检查 /torrent/status 的 state 和 peers
   true  → 继续排查

2. curl /stream/<hash> -H "Range: bytes=0-1023"
   503 → checking_files，等 10s 自动重试
   416 → hole，libtorrent 正在下载
   206 → 服务端正常，问题在浏览器

3. 浏览器 DevTools → Console
   检查 video.src 和 network 请求
```

### 症状：播放卡顿 / seek 卡住

```
1. 检查 logs/video-stream.log
   hole=true + elapsed>=2.0s → 下载太慢或死种

2. 检查 logs/torrent-engine.log
   peers=0 → 死种，换 magnet
   download_rate=0 → 无活跃 peer

3. 检查文件 sparse 状态
   stat --format="逻辑=%s 实际=%b*%B" cache/torrent/<hash>/.../*.mp4
```

### 症状：后端 502

```
1. journalctl -u caddy-claw | grep "502\|timeout"
2. journalctl -u star-archive-frontend | grep "error\|warn"
3. 检查 Nuxt 是否崩溃重启过多次
```

---

## 4. 常用命令

```bash
# 实时查看后端日志
journalctl -u star-archive-backend -f

# 查看 torrent 状态
curl -s http://localhost:8765/torrent/status/<hash> | python3 -m json.tool

# 检查缓存大小
du -sh cache/torrent/* 2>/dev/null | sort -rh | head -10

# 测试 stream 某段
curl -s --range "bytes=0-1023" http://localhost:8765/stream/<hash> | wc -c

# 检查 libtorrent 版本
python3 -c "import libtorrent; print(libtorrent.version)"
```
