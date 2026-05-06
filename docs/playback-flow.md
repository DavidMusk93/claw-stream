# 播放流程详解

## 完整交互图

```
用户点击播放
    │
    ▼
+----------------------------+
│ 1. GET /api/check/<hash>   │
│    检查头部是否就绪        │
+----------------------------+
    │
    ├─ head_ready = true ──┐
    │                       ▼
    │              +---------------------+
    │              │ video.src = /stream │
    │              │ 直接播放本地文件    │
    │              +---------------------+
    │                       │
    │                       ▼
    │              [canplay 事件触发]
    │                       │
    │                       ▼
    │              播放开始 ✅
    │
    └─ head_ready = false ─┐
                            ▼
              +---------------------------+
              │ 2. POST /torrent/add      │
              │    启动 torrent 下载      │
              +---------------------------+
                            │
                            ▼
              +---------------------------+
              │ 3. 显示"连接种子..."      │
              │    等待 metadata          │
              +---------------------------+
                            │
                            ▼
              +---------------------------+
              │ 4. 轮询 /torrent/status   │
              │    每 1.5s 一次           │
              +---------------------------+
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
      [checking_files]  [downloading]   [head_ready]
              │             │             │
              ▼             ▼             ▼
         显示"校验中"   显示进度条       清除轮询
         继续等待       Peers/速率/％    video.src=/stream
                                              │
                                              ▼
                                     [canplay 事件触发]
                                              │
                                              ▼
                                     播放开始 ✅
```

## 服务器内部状态机

```
add_torrent(magnet)
    │
    ▼
+---------------+     +-------------------+     +------------------+
│ metadata_wait │ --> │ checking_files    │ --> │ downloading      │
│ (等种子信息)  │     │ (校验已有数据)    │     │ (按优先级下载)   │
+---------------+     +-------------------+     +------------------+
       │                      │                          │
       │                      │                          ▼
       │                      │                 +------------------+
       │                      │                 │ head pieces      │
       │                      │                 │ urgent deadline  │
       │                      │                 +------------------+
       │                      │                          │
       │                      │                          ▼
       │                      │                 +------------------+
       │                      │                 │ rest pieces      │
       │                      │                 │ priority 0       │
       │                      │                 │ (暂停下载)       │
       │                      │                 +------------------+
       │                      │
       │                      ▼
       │            torrent_checked_alert
       │                      │
       │                      ▼
       │            重新 apply_play_priority()
       │            （确保 deadline 生效）
       │
       ▼
   metadata_received_alert
       │
       ▼
   _on_metadata()
   - 选最大视频文件
   - 预缓存: 前 2% pieces = 4
   - 播放模式: 全部 pieces = 0（等播放按钮触发）
```

## 预缓存 vs 播放模式对比

```
┌─────────────────────────────────────────────────────────────┐
│                        预缓存模式                           │
│ 触发: 页面加载后自动                                        │
│ 范围: 最新 13 部作品                                        │
│ 策略:                                                        │
│   piece 0 ~ 2%:  priority = 4   ← 只下载头部               │
│   piece 2% ~ end: priority = 0  ← 不下载                   │
│ 目的: 让播放按钮显示绿色徽章，点击即可播放                  │
│ 磁盘: ~100-200MB/部                                         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                        播放模式                             │
│ 触发: 用户点击播放按钮                                      │
│ 条件: 头部未就绪                                            │
│ 策略:                                                        │
│   piece head ~ head+20: priority = 7 + deadline=0  ← urgent│
│   piece rest:           priority = 0               ← 暂停  │
│ 目的: 40MB 头部就绪即可播放，不占用带宽下载整部             │
│ 边下边播: 播放后逐渐降低头部限制，允许继续下载              │
└─────────────────────────────────────────────────────────────┘
```

## 稀疏文件读取流程

```
浏览器请求 /stream/<hash>
         │
         ▼
+----------------------------+
│ find_video_state(hash)     │
│ - 遍历 cache/torrent/<hash>│
│ - 找最大视频文件           │
│ - 检查 head_ready:         │
│   前 512KB 有 ftyp?        │
+----------------------------+
         │
    head_ready = false
         │
         ▼
    HTTP 404 "head not ready"

    head_ready = true
         │
         ▼
+----------------------------+
│ Range 请求处理             │
│ - open(path, "rb")         │
│ - seek(start)              │
│ - read(chunk)              │
│                            │
│ 稀疏文件特性:              │
│ - 已下载区域 → 真实数据    │
│ - 未下载区域 → 自动读 0    │
│ - 浏览器播放器会缓冲等待   │
+----------------------------+
         │
         ▼
    HTTP 206 video/mp4
```

## 常见问题速查

```
问题: 点击播放 → "加载超时，请检查文件完整性"

排查链:
  1. curl /api/check/<hash> | head_ready = ?
     └─ false → 头部还没下载完，等 30-60s
     └─ true  → 继续排查

  2. curl /stream/<hash> -H "Range: bytes=0-1023"
     └─ 404 → head_ready 检测和 stream 不一致，重启服务器
     └─ 206 → 服务器正常，问题在浏览器

  3. 浏览器问题:
     └─ 真实浏览器无法播放 → MP4 格式问题（moov 不在头部）
     └─ headless 浏览器无法播放 → 正常，headless 不支持 H.264

### canplay 竞态条件

```
错误顺序（事件丢失）:
  video.src = '/stream/hash'   ← 浏览器立即开始加载
       │
       ▼  <-- canplay 在这里触发！
  video.addEventListener('canplay', ...)  ← 监听器还没绑定！
       │
       ▼
  永远等不到 canplay → 超时 ❌

正确顺序（先绑定后设 src）:
  video.addEventListener('canplay', ...)  ← 先绑定
  video.src = '/stream/hash'              ← 再设 src
  video.load()                            ← 强制重新加载
       │
       ▼
  canplay 触发 → 监听器捕获 → 播放 ✅
```

### moov 完整检测

```
旧 head_ready（导致假阳性）:
  读取 512KB → 找到 ftyp → 认为就绪
       │
       ▼
  moov 实际 12MB，不在 512KB 内
  浏览器解析到不完整 moov → 卡住 ❌

新 head_ready（扫描 box 结构）:
  读取 16MB → 扫描 MP4 box
       │
       ▼
  找到 moov → 计算 moov_end
       │
       ▼
  确认 moov_end 附近 1KB 非 0
       │
       ▼
  moov 完整 → 浏览器可解析 ✅
```

### moov 位置分布

```
faststart MP4（约 50%）:
  [ftyp 32B][moov 9-12MB][mdat 5GB]
       │
       └── head_ready 可检测

非 faststart MP4（约 50%）:
  [ftyp 32B][mdat 5GB][moov 9-12MB]
       │
       └── 需要完整下载才能播放
       └── head_ready 永远 false
       └── 前端提示"需完整下载"
```

问题: 无脑下载整部视频，磁盘瞬间爆满

排查链:
  1. 看 log: "play priority: ... head=20pcs" 还是 "full speed"
     └─ 后者 → 代码没更新到最新版本

  2. 用脚本验证 piece 优先级:
     handle.get_piece_priorities()
     └─ 全部非 0 → prioritize_files() 覆盖了
     └─ 只有 20 个非 0 → 正常

问题: 重新 add 后 checking_files 很久

原因: 之前下载过同 hash 的文件，libtorrent 在逐块校验
解决: 等它完成（通常 30-60s），或 DELETE 后重新下载
```


## 稀疏文件空洞陷阱（seeking 卡住）

### 现象
点击播放后显示"定位中..."，然后永远卡住，`seeked` / `canplay` 永远不触发。

### 根因链

```
预缓存只下载前 2% pieces (~5-40MB)
         │
         ▼
/api/check 返回 head_ready=true（moov 已下载）
         │
         ▼
浏览器开始播放，发送 Range 请求
         │
         ▼
_serve_video 读取稀疏文件
         │
         ▼
未下载区域 → read() 返回全 0
         │
         ▼
浏览器收到 0 字节
         │
         ▼
MP4 解析失败 / seek 无法完成
         │
         ▼
seeked 不触发 → 永远"定位中"
```

### 关键认知

1. **稀疏文件的空洞返回 0，不是 EOF**
   - Linux 稀疏文件未分配区域 `read()` 返回 `\x00` 填充
   - `if not buf:` 不会触发，数据会正常发给浏览器
   - 浏览器解析 0 数据 = 无效的 MP4 box → 解析器崩溃或无限等待

2. **为什么不是"缓冲中"而是"定位中"**
   - `video.load()` 触发 `seeking`（seek 到时间 0）
   - 浏览器在 seek 过程中需要读取时间 0 的数据
   - 如果读到 0，认为 seek 还没完成 → `seeked` 不触发
   - 不会进入 `waiting` 状态，所以不会显示"缓冲中"

3. **为什么头部 urgent 下载了还会遇到空洞**
   - 头部 30 pieces = 60MB 是 urgent 的，但下载需要时间
   - 如果用户点击播放时 urgent pieces 还没下载完，就会遇到空洞
   - 或者 `/api/check` 之前检测通过，但播放时某些 piece 刚好还没落盘

### 修复方案

**后端**：`_serve_video` 读取时检测空洞，遇到全 0 停止发送

```python
buf = f.read(min(16384, remaining))
if not any(buf):  # 全 0 → 空洞
    break           # 停止发送，让浏览器重试
```

- 用 16KB 小块读取，更容易精确定位到空洞边界（piece = 2MB）
- 不发送 0 数据给浏览器，避免解析器崩溃
- 浏览器收到不完整响应后会自动重试

**前端**：`seeking` 事件也启动状态轮询

```javascript
modalVideo.addEventListener('seeking', function(){
  modalLoading.innerHTML = '<span>定位中...</span>';
  startStatusPoll();  // 实时显示速率和进度
});
```

- 之前只在 `waiting` 事件轮询，`seeking` 时没有进度反馈
- 现在即使卡在 seek，用户也能看到 `缓冲中 | X.X MB/s | 已缓存 X MB`

### 验证方法

```bash
# 检查缓存文件实际大小 vs 逻辑大小
ls -ls cache/torrent/<hash>/
# 第一列是实际块数 * 512 = 实际字节数
# 如果实际 << 逻辑，说明大部分是空洞
```

### 相关修复

- `get_status()` 增加 `try/except RuntimeError` 防御，避免 torrent handle 被销毁后查询导致 500
