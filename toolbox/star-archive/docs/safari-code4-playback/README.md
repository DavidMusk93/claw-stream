# Safari code=4 播放失败根因分析

> **关键词：** MEDIA_ERR_SRC_NOT_SUPPORTED, libtorrent checking_files, sparse file, MP4 demuxer

---

## 现象

视频文件已下载 ~99%（`real_size=3849969664/3849944387`），后端所有 `/stream/` 请求返回 206 + 有效数据，ffprobe 验证文件为有效 MP4/H.264，moov 区域完整。但 Safari 仍报 `code=4`（`MEDIA_ERR_SRC_NOT_SUPPORTED`），重试多次无效。

Chrome 播放正常。

---

## 排查时间线

```
┌─────────────────────────────────────────────────────────────────────┐
│  Step 1: 检查后端响应                                                │
│  → 206 Partial Content, Content-Range 正确, 数据长度匹配             │
│  → 排除 HTTP 协议层问题                                              │
├─────────────────────────────────────────────────────────────────────┤
│  Step 2: ffprobe 直接验证文件                                        │
│  → 有效 MP4/H.264 + AAC, moov=[36, 7627023] 完整                    │
│  → 排除文件本身损坏                                                  │
├─────────────────────────────────────────────────────────────────────┤
│  Step 3: 模拟 Safari 请求模式拼接                                    │
│  → Safari 发送: 0-1, 0-3849944386, 3014656-..., 7602176-...         │
│  → 后端截断为 1MB chunk 返回                                         │
│  → 顺序拼接后 ffprobe 报 "contradictionary STSC and STCO"            │
│  → 怀疑 range 重叠/截断导致拼接问题                                  │
├─────────────────────────────────────────────────────────────────────┤
│  Step 4: 重新审视拼接方式                                            │
│  → 发现 Safari 的第二次请求是 0-3849944386（整个文件）               │
│  → 后端截断为 0-1048575，但 Safari 可能期望收到完整文件？            │
│  → 排除：HTTP 206 明确允许截断，Safari 会发后续 range 补齐           │
├─────────────────────────────────────────────────────────────────────┤
│  Step 5: 检查日志中的 hole 标记                                      │
│  → stream-router.log 中大量 hole=true                                │
│  → 但 hole=true 时后端仍然返回 206 + 数据（日志字段不影响响应）       │
│  → 深入：hole 检测用 "not any(data)"，对 MP4 前 2 字节 00 00 误判   │
│  → 这是日志误导，不是根本原因                                        │
├─────────────────────────────────────────────────────────────────────┤
│  Step 6: 观察 torrent 状态                                           │
│  → video-stream.log: state=checking_files                            │
│  → libtorrent 在 checking_files 期间会做什么？                       │
│  → 答案：临时清零/修改文件中的 piece，验证 hash                      │
├─────────────────────────────────────────────────────────────────────┤
│  Step 7: 锁定根因                                                    │
│  → checking_files + read_video_range 并发                           │
│  → read_video_range 读到被 libtorrent 临时清零的区域                  │
│  → 返回全零数据给 Safari                                            │
│  → Safari MP4 demuxer 解析到全零 chunk → code=4                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 根因详解

### libtorrent checking_files 的行为

当 torrent 被添加到 session 时（尤其是从缓存目录加载已有文件），libtorrent 会进入 `checking_files` 状态：

1. 读取文件中的每个 piece
2. 计算 piece hash
3. 与 torrent metadata 中的 hash 对比
4. **如果 hash 不匹配，将 piece 标记为未下载（清零或删除）**

在这个过程中，文件内容可能被临时修改。如果我们同时从文件中读取数据用于 HTTP 流式传输，就可能读到不一致的全零数据。

### 为什么 Chrome 正常而 Safari 报错

Chrome 的媒体解析器可能对数据不一致更容错，或者重试逻辑不同。Safari 的 MP4 demuxer（基于 AVFoundation）对数据一致性要求严格，遇到全零 chunk 立即报 `MEDIA_ERR_SRC_NOT_SUPPORTED`。

### 为什么 SEEK_HOLE 没检测出来

`SEEK_HOLE` 检测的是 sparse file 的 hole（未分配磁盘块的区域）。libtorrent 清零的是已分配磁盘块的区域，只是内容变成了全零。所以 `SEEK_HOLE` 认为"这里有数据"，但实际上数据是无效的。

---

## 修复方案

核心原则：**checking_files 期间禁止流式读取**。

### 1. `/api/check/{hash}` — 延迟就绪信号

```python
def check_stream(hash_str: str, engine: Any = Depends(get_engine)):
    local_path, local_size, head_ready_fs, mime = find_video_state(hash_str)
    # 若 torrent 处于 checking_files，即使文件系统有数据也报告未就绪
    head_ready = head_ready_fs and not _is_torrent_checking(engine, hash_str)
    return StreamCheckResponse(head_ready=head_ready, ...)
```

### 2. `/stream/{hash}` — 503 拒绝服务

```python
def stream_video(hash_str: str, request: Request, engine: Any = Depends(get_engine)):
    path, real_size, head_ready, mime = find_video_state(hash_str)
    if _is_torrent_checking(engine, hash_str):
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": "10"},
            detail="Torrent checking files"
        )
    # ... 正常流式传输
```

### 3. 前端行为

前端 `waitForHeadReady` 轮询 `/api/check/`，在 `head_ready=false` 时继续等待。`/stream/` 返回 503 时，浏览器会自动重试（HTTP 503 + Retry-After 标准行为）。

---

## 教训与反思

### 不要假设"文件系统有数据"="数据可安全读取"

torrent 客户端不是静态文件服务器。它的内部状态（checking、downloading、seeding）会直接影响文件一致性。

### 并发访问 sparse file 的风险

sparse file + 动态下载引擎的组合，使得"文件存在"和"文件可读"是两个不同的概念。需要额外的状态机来协调。

### Safari 是更严格的测试平台

Chrome 可能容错一些数据不一致的情况，但 Safari 不会。如果 Safari 能播，Chrome 一定能播；反之不成立。用 Safari 作为兼容性基准更可靠。

### 日志误导

`not any(data)` 对 MP4 `00 00` 开头的误判，让我们在排查初期走了很多弯路。数据验证逻辑必须与文件格式解耦。

---

## 参考

- [libtorrent documentation — torrent_status](https://libtorrent.org/reference-Core.html#torrent-status)
- [Apple Developer — AVErrorMediaDiscontinuity](https://developer.apple.com/documentation/avfoundation/averror/averrormediadiscontinuity)
- [RFC 7233 — HTTP Range Requests](https://tools.ietf.org/html/rfc7233)
