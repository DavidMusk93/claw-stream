# Seek 跳转支持

## 需求背景

视频播放器原生支持拖动进度条、快进快退。但当用户 seek 到**未下载的区域**时，稀疏文件会读到 0，导致画面卡顿或黑屏。

传统做法是完整下载整部视频后再播放。我们的 lazy 策略只下载头部 40MB，其余暂停。因此 seek 必须能触发**按需 urgent 下载**。

## 架构设计

```
+--------------------------------------------------------+
|                    播放器 (浏览器)                      |
|  用户拖动进度条到 30:00                                 |
|       │                                                |
|       ▼                                                |
|  video.currentTime = 1800                              |
|       │                                                |
|       ▼                                                |
|  浏览器自动发送 Range: bytes=450MB-451MB               |
+--------------------------------------------------------+
                          │
                          ▼
+--------------------------------------------------------+
|                    Cache Server                        |
|  1. 解析 Range: start=450MB, end=451MB                 |
|       │                                                |
|       ▼                                                |
|  2. 计算 piece 范围                                    |
|     piece_length = 2MB                                 |
|     start_piece = 450MB/2MB - 2 = 223                  |
|     end_piece   = 451MB/2MB + 2 = 227                  |
|       │                                                |
|       ▼                                                |
|  3. 设置 urgent deadline                               |
|     for p in 223..227:                                 |
|       handle.set_piece_deadline(p, 0)                  |
|       │                                                |
|       ▼                                                |
|  4. HTTP 206 返回数据                                  |
|     (libtorrent 后台 urgent 下载这些 piece)            |
+--------------------------------------------------------+
```

## 为什么需要 piece-level 控制

```
错误做法：Range 请求只管读取文件，不通知下载引擎

  用户 seek 到 30:00 ──────► 读取 bytes=450MB
         │
         ▼
  文件是稀疏文件！
  450MB 位置 = 全 0（空洞）
         │
         ▼
  浏览器读到 0 ────────────► 黑屏/卡顿 ❌

正确做法：Range 请求同时触发 urgent 下载

  用户 seek 到 30:00 ──────► 读取 bytes=450MB
         │                           │
         │                           ▼
         │              set_piece_deadline(223..227, 0)
         │                           │
         │                           ▼
         │              libtorrent 立即下载这些 pieces
         │                           │
         ▼                           ▼
  读取 450MB ◄───────────── piece 已就绪 ✅
```

## 缓冲策略

为了防止 seek 后瞬间进入 waiting 状态，我们在 seek 区域前后各加 **2 个 piece 缓冲**（约 8MB）：

```
用户 seek 到 piece 100

下载范围：piece 98 ~ piece 102
         │              │
    [缓冲]          [缓冲]
    8MB前            8MB后

这样用户快进/快退几秒内不会再次触发 waiting
```

## 前端交互

### 键盘快捷键

```
┌─────────────────────────────────────┐
│  按键          │  功能               │
├─────────────────────────────────────┤
│  ← ArrowLeft   │  后退 10 秒         │
│  → ArrowRight  │  前进 10 秒         │
│  ESC           │  关闭播放器         │
│  进度条点击    │  跳转到任意位置     │
└─────────────────────────────────────┘
```

### 状态提示

```
seeking 事件触发
    │
    ▼
┌──────────────────┐
│  定位中...       │  ← 用户拖动进度条时显示
└──────────────────┘
    │
    ▼
seeked 事件触发
    │
    ├─ 如果已暂停 ──► 保持显示，等待 play
    │
    └─ 如果正在播放 ─► 隐藏提示
         │
         ▼
    waiting 事件触发（数据还没下载完）
         │
         ▼
    ┌──────────────────┐
    │  缓冲中...       │  ← libtorrent 正在 urgent 下载
    └──────────────────┘
         │
         ▼
    playing 事件触发
         │
         ▼
         隐藏提示 ✅
```

## 代码实现

### 服务器端

```python
def _seek_priority(self, hash_str, start_byte, end_byte):
    """根据 Range 请求设置对应 pieces 为 urgent"""
    info = self.engine.torrents.get(hash_str)
    h = info["handle"]
    ti = h.torrent_file()
    fs = ti.files()
    idx = info["video_idx"]
    piece_length = ti.piece_length()
    file_offset = fs.file_offset(idx)
    num_pieces = ti.num_pieces()

    # 计算 piece 范围（+/- 2 pieces 缓冲）
    start_piece = max(0, (file_offset + start_byte) // piece_length - 2)
    end_piece = min(num_pieces - 1, (file_offset + end_byte) // piece_length + 2)

    for p in range(start_piece, end_piece + 1):
        h.set_piece_deadline(p, 0)

def _serve_video(self, hash_str):
    # ... 解析 Range ...
    if range_hdr:
        # Seek 到未下载区域时，通知 libtorrent urgent 下载
        self._seek_priority(hash_str, start, end)
        # ... 返回 206 ...
```

### 前端

```javascript
// 键盘快捷键
document.addEventListener('keydown', function(e){
  if(!modalOverlay.classList.contains('active')) return;
  if(e.key === 'ArrowLeft'){
    modalVideo.currentTime -= 10;
  } else if(e.key === 'ArrowRight'){
    modalVideo.currentTime += 10;
  } else if(e.key === 'Escape'){
    closeModal();
  }
});

// Seek 状态提示
modalVideo.addEventListener('seeking', function(){
  modalLoading.style.display = 'flex';
  modalLoading.innerHTML = '<span>定位中...</span>';
});

modalVideo.addEventListener('seeked', function(){
  if(!modalVideo.paused){
    modalLoading.style.display = 'none';
  }
});
```

## 测试验证

```bash
# 1. seek 到 1MB 位置
curl -H "Range: bytes=1048576-1049599" \
  http://localhost:8765/stream/<hash>
# => 206 Partial Content, 1024 bytes

# 2. seek 到 100MB 位置
curl -H "Range: bytes=104857600-104858623" \
  http://localhost:8765/stream/<hash>
# => 206 Partial Content, 1024 bytes

# 验证：服务器日志中会显示 libtorrent 正在下载对应 pieces
```

## 边界情况

| 场景 | 处理 |
|------|------|
| seek 到已下载区域 | 直接读取，不触发额外下载 |
| seek 到头部 | 头部已经 urgent，无额外开销 |
| seek 到尾部 | 计算 piece 范围，触发 urgent |
| 快速连续 seek | 每次 seek 都触发新的 deadline，libtorrent 会自动合并 |
| seek 超出文件大小 | 浏览器控制，服务器返回 416 Range Not Satisfiable |
