# 追溯日志与故障排查体系

## 1. 概述

本系统由多个独立脚本协同工作，日志分散在 stdout/stderr 和浏览器控制台。当播放卡住、数据缺失或刷新失败时，需要根据症状快速定位到具体组件。

**当前架构**

```
search-news.py   →  /tmp/actress-news/
fetch-jable.py   →  /tmp/actress-jable/
generate-report.js → actresses-report.html
cache-server.py  →  HTTP :8765 + libtorrent 引擎
```

## 2. 日志体系概览

### 2.1 各组件日志输出

```
┌──────────────────┬─────────────────────┬──────────────────────────┐
│ 组件             │ 输出位置            │ 格式约定                 │
├──────────────────┼─────────────────────┼──────────────────────────┤
│ search-news.py   │ stdout + stderr     │ [news] tag, 进度条风格   │
│ fetch-jable.py   │ stdout              │ [jable] tag, 逐条 ✅/⚠️ │
│ generate-report.js│ stdout + stderr    │ [filter]/[save]/[report] │
│ cache-server.py  │ stdout (终端)       │ [torrent]/[serve]/[remove]│
│ refresh.sh       │ stdout              │ 步骤编号 [1/3] [2/3]... │
│ 浏览器前端       │ DevTools Console    │ [prefetch]/Play error    │
└──────────────────┴─────────────────────┴──────────────────────────┘
```

### 2.2 关键日志标识速查

| 标识 | 来源 | 含义 |
|------|------|------|
| `[news]` | search-news.py | 抓取 ijavtorrent 数据进度 |
| `[jable]` | fetch-jable.py | 抓取 jable.tv 封面和 m3u8 |
| `[filter]` | generate-report.js | 过滤后的女演员数量 |
| `[save]` | generate-report.js | 封面 base64 转存失败 |
| `[report]` | generate-report.js | HTML 生成完成报告 |
| `[torrent]` | cache-server.py | torrent 添加/优先级/预缓存 |
| `[serve]` | cache-server.py | HTTP 视频流请求日志 |
| `[remove]` | cache-server.py | 删除 torrent 出错 |
| `[prefetch]` | 浏览器 | 页面加载后预缓存 magnet 数量 |
| `Play error` | 浏览器 | 视频播放失败（通常是权限/格式） |
| `Cache poll error` | 浏览器 | /torrent/status 轮询失败 |

## 3. 排查决策树

按**用户可见症状**从上到下排查：

```
症状：播放按钮点击后无反应 / 黑屏
    |
    v
+---------------------------------------------------------------+
|1. 浏览器 DevTools → Console                                   |
|   有无 "Play error" 或网络错误？                              |
+---------------------------------------------------------------+
    |
    |-- 有 Play error --|
    |                   v
    |          检查 video.src 是否有效
    |          → /stream/<hash> 返回 404?
    |                   |
    |                   v
    |          转到排查分支 A: 视频流 404
    |
    |-- 无错误，但黑屏 --|
                        v
+---------------------------------------------------------------+
|2. 检查 /torrent/status/<hash> 轮询结果                        |
|   浏览器 Network 面板能看到轮询请求吗？                        |
+---------------------------------------------------------------+
    |
    |-- 轮询正常，state=downloading --|
    |                                  v
    |                         head_ready = false?
    |                                  |
    |                                  v
    |                         转到排查分支 B: 头部未就绪
    |
    |-- 轮询返回 404 或 error --|
                               v
+---------------------------------------------------------------+
|3. cache-server 终端日志                                       |
|   有无 "invalid torrent handle used"？                        |
+---------------------------------------------------------------+
    |
    |-- 有 --|
    |        v
    |   torrent 已被移除或 session 重启
    |   → 重新点击播放，或刷新页面
    |
    |-- 无 --|
             v
    转到排查分支 C: 服务端异常


症状：页面数据老旧 / 女演员作品缺失
    |
    v
+---------------------------------------------------------------+
|1. 检查 /tmp/actress-news/ 时间戳                              |
|   ls -lt /tmp/actress-news/*.json | head                      |
+---------------------------------------------------------------+
    |
    |-- 时间很旧 (>7天) --|
    |                     v
    |            运行 ./refresh.sh 或点 🔄
    |            观察输出是否有 ❌/⚠️
    |
    |-- 时间新鲜 --|
                  v
+---------------------------------------------------------------+
|2. 检查 generate-report.js 输出                                |
|   [filter] Solo: N actresses 的 N 是否正确？                  |
+---------------------------------------------------------------+
    |
    |-- N 偏小 --|
    |            v
    |   config.json 中 type="solo" 过滤导致
    |   → 检查 config.json actresses 列表
    |
    |-- N 正确 --|
                 v
    检查 /tmp/actress-news/<code>.json 是否包含预期作品
    → 若无：search-news.py 抓取逻辑变化（见分支 D）
    → 若有：generate-report.js 读取路径问题


症状：刷新按钮点击后失败
    |
    v
+---------------------------------------------------------------+
|1. 浏览器 Toast 提示内容                                       |
|   "刷新失败: ..." 具体信息是什么？                            |
+---------------------------------------------------------------+
    |
    |-- "Forbidden: local access only" --|
    |                                     v
    |   你通过非本地 IP 访问 (如公网/Nginx 代理)
    |   → /api/regenerate 只允许 loopback/private IP
    |   → 解决方案：SSH 到服务器执行 ./refresh.sh
    |
    |-- 其他错误 --|
                  v
+---------------------------------------------------------------+
|2. 直接终端执行 ./refresh.sh                                   |
|   观察哪一步报错                                               |
+---------------------------------------------------------------+
    |
    |-- search-news.py 失败 --|
    |                         v
    |   Playwright 浏览器未安装？
    |   → uv run playwright install chromium
    |
    |-- fetch-jable.py 失败 --|
    |                         v
    |   jable.tv 被墙或反爬？
    |   → 检查网络，或暂时跳过此步骤
    |
    |-- generate-report.js 失败 --|
                               v
    Node.js 未安装或 /tmp 数据缺失
```

## 4. 分支排查手册

### 分支 A：视频流 404

```
症状：点击播放后 video.src = /stream/<hash>，但返回 404

排查步骤：
1. 确认 hash 正确（40 位十六进制）
2. 检查 cache/torrent/<hash>/ 目录是否存在
3. 目录存在但无视频文件？
   → torrent 仍在 metadata_wait 阶段
   → 查看 cache-server 终端 [torrent] added: 日志
4. 目录存在且有视频文件？
   → find_video_state() 中 moov 检测失败
   → 文件可能损坏或非 MP4 格式
```

### 分支 B：头部未就绪 (head_ready = false)

```
症状：/torrent/status 返回 head_ready: false，进度条走得很慢

根因分类：
┌─────────────────────────────────────────────────────────────┐
│ 类型 1：moov 在尾部（非 faststart）                          │
│   → 必须下载完整视频才能播放                                 │
│   → 特征：progress 缓慢上升，state=downloading               │
│   → 判断：cache-server 无 "play priority" 日志               │
│   → 解决：无解，等待下载完成或换种子                         │
├─────────────────────────────────────────────────────────────┤
│ 类型 2：moov 在头部但 piece 未下载                           │
│   → 特征：progress < 5%，peers 少                            │
│   → 检查：/torrent/status 中 peers 数量                      │
│   → 解决：peers=0 说明死种，换 magnet                        │
├─────────────────────────────────────────────────────────────┤
│ 类型 3：稀疏文件空洞陷阱（已修复）                           │
│   → _serve_video 中 16KB 块级检测全 0 即 break               │
│   → 若仍卡住，检查 Linux 内核版本和文件系统                  │
└─────────────────────────────────────────────────────────────┘
```

### 分支 C：服务端异常

```
症状：cache-server 终端报错或 HTTP 500

常见错误及处理：

[invalid torrent handle used]
  → 原因：torrent 被 remove_torrent() 后仍查询状态
  → 已修复：get_status() 捕获 RuntimeError 返回 None
  → 若仍出现：检查是否有并发 remove + status 查询

[remove] error: ...
  → 原因：删除目录时文件被占用
  → 解决：停止播放后再删除，或重启 cache-server

libtorrent 崩溃 / segfault
  → 原因：libtorrent 2.0.8 已知问题，大文件或特殊种子触发
  → 解决：重启 cache-server，如频繁发生考虑降级到 1.2.x
```

### 分支 D：search-news.py 抓取异常

```
症状：/tmp/actress-news/ 中某女演员作品数量明显减少

排查：
1. 直接运行 uv run search-news.py config.json
2. 观察该女演员的输出：
   ✅ 正常："  ✅ 白峰ミウ: 5 作品, 5 封面"
   ⚠️ 异常："  ⚠️ 白峰ミウ: TimeoutError"

常见异常：
┌─────────────────────────────────────────────────────────────┐
│ TimeoutError                                                 │
│   → ijavtorrent.com 访问超时                                 │
│   → 检查网络，或增加 page.goto timeout                       │
├─────────────────────────────────────────────────────────────┤
│ 作品数量对不上                                               │
│   → 网站结构调整，选择器失效                                 │
│   → 检查 search-news.py 中 querySelector 是否匹配新 DOM      │
├─────────────────────────────────────────────────────────────┤
│ 封面全部缺失 (no cover)                                      │
│   → CDN 域名变更或图片反爬                                   │
│   → 检查 cover URL 是否 403，考虑换 User-Agent               │
└─────────────────────────────────────────────────────────────┘
```

## 5. 快速诊断命令

```bash
# 检查各组件输出目录状态
ls -lt /tmp/actress-news/*.json | head -5
ls -lt /tmp/actress-jable/*.json | head -5

# 检查 torrent 缓存状态
du -sh toolbox/star-archive/cache/torrent/* 2>/dev/null | sort -rh | head -10

# 检查 cache-server 是否运行
curl -s http://localhost:8765/api/cache | python3 -m json.tool

# 测试单个 torrent 状态
curl -s http://localhost:8765/torrent/status/<40位hash>

# 手动执行刷新（观察完整输出）
cd toolbox/star-archive && bash -x refresh.sh

# 检查 libtorrent 版本
python3 -c "import libtorrent; print(libtorrent.version)"
```

## 6. 日志增强建议

当前日志均为 `print()` 输出，建议未来迭代：

1. **统一日志文件**：cache-server 增加 `--log-file` 参数，将 `[torrent]`/`[serve]` 写入滚动日志
2. **结构化日志**：JSON 格式日志，便于 `jq` 过滤和分析
3. **前端错误上报**：浏览器 `console.error` 增加 `fetch('/api/log', ...)` 发送到服务端
4. **关键指标暴露**：`/api/metrics` 返回 torrent 数量、下载速率、缓存命中率等
