# Cache Server 使用指南

## 架构概述

```
+-------------------------------------------------------------+
|                     Cache Server (8765)                     |
|  +----------------+    +----------------+    +-----------+  |
|  | libtorrent     |    | HTTP Handler   |    | Cache Mgr |  |
|  | session        |--->|                |<---|           |  |
|  +----------------+    +----------------+    +-----------+  |
|         |                       |                           |
|         v                       v                           |
|  cache/torrent/<hash>/    /stream/<hash>                   |
|  (sparse files)           (Range 206)                      |
+-------------------------------------------------------------+
```

**核心设计**：libtorrent 负责下载，HTTP 直接读取本地稀疏文件。播放不走 piece 组装流。

## 目录结构

```
toolbox/star-archive/
├── cache-server.py          # 一体化服务器
├── cache/
│   └── torrent/
│       └── <hash>/          # libtorrent 下载目录（稀疏文件）
│           └── <video>.mp4  # 逻辑大小 = 完整视频，实际磁盘 = 已下载
├── generate-report.js       # HTML 报告生成器
└── docs/                    # 本文档
```

## API 端点

```
POST /torrent/add            添加 magnet 开始下载
GET  /torrent/status/<hash>  查询下载状态
GET  /stream/<hash>          HTTP 206 Range 播放（直接读本地文件）
GET  /api/check/<hash>       检查头部是否就绪（可播放）
GET  /api/cache              列出所有任务 + 状态
DELETE /api/cache/<hash>     删除 torrent + 本地文件
```

## 下载策略（Lazy）

```
预缓存模式 (prefetch=true)          播放模式 (prefetch=false)
+------------------------+          +------------------------+
| 页面加载后自动触发     |          | 用户点击播放按钮触发   |
| 只下载最新 13 部作品   |          | 头部 20 pieces urgent  |
| 每部只下前 2% pieces   |          | 其余 pieces 优先级 0   |
| ~100-200MB/部          |          | ~40MB 即可开始播放     |
+------------------------+          +------------------------+
```

## 关键设计决策

### 1. 为什么不用 `prioritize_files()`

```
错误做法：
  prioritize_files([0, 7])   # 文件0=0, 文件1=7
  prioritize_pieces([7,7,0,0])  # ← 被上面覆盖！

结果：piece 0 跨越了文件0和文件1，
      因为文件0优先级=0，libtorrent 拒绝下载 piece 0
      → 视频头部永远下不了

正确做法：
  // 不调用 prioritize_files()
  prioritize_pieces([7,7,0,0])  # 直接用 piece 级控制
```

### 2. 为什么检查 `head_ready` 而不是 `local_size > 1MB`

```
libtorrent 默认 rarest-first（最稀有优先）

文件分布：
  [0][0][0][0][0][0] ... [data][0][data][data][0] ... [data]
  ↑头部                    ↑随机分布                  ↑尾部

local_size = 2GB 不代表头部有数据！
头部可能是全 0（稀疏文件空洞）→ 浏览器读到 0 → 播放失败

head_ready 检查：
  读取前 512KB，找 ftyp 标志（MP4 faststart 格式）
  ftyp 存在 → 头部就绪 → 可播放
```

### 3. checking_files 陷阱

```
有旧数据的 torrent 重新 add 时：

  state: checking_files ────────→ state: downloading
         ↑                              ↑
    libtorrent 在验证                 验证完成
    磁盘已有数据                      开始响应 deadline

陷阱：checking_files 期间
      set_piece_deadline() 不生效！
      必须等 torrent_checked_alert 后再重新设置
```

## 启动

```bash
cd toolbox/star-archive
python3 cache-server.py --port 8765
```

浏览器访问 http://localhost:8765/

## 常见排查

| 现象 | 原因 | 解决 |
|------|------|------|
| 头部全 0 | prioritize_files 覆盖了 piece 优先级 | 移除 prioritize_files 调用 |
| 播放超时 | checking_files 期间 deadline 不生效 | 等 checking 完成（30-60s） |
| 进度 100% 但头部 0 | moov 在尾部，不是 faststart | 这类文件无法边下边播 |
| 无脑下载全部 | piece_prios 全设为 7 | 只设头部 20pcs=7，其余=0 |
