# AGENTS.md — Star Archive 项目指南

## 项目概览

**Star Archive** 是一个基于 BitTorrent 的本地视频流式播放系统。核心功能：通过 libtorrent 下载视频文件，同时支持 HTTP Range 请求流式播放，实现"边下边播"。

**技术栈：**
- 后端：Python 3.11 + FastAPI + libtorrent 2.0.8.0
- 前端：Nuxt 3 + Vue 3
- 数据库：DuckDB
- 部署：systemd + Caddy 反向代理

---

## 关键架构决策

### Sparse File + SEEK_DATA/SEEK_HOLE

Linux sparse file 是核心存储策略。文件以完整大小创建，但只有实际下载的数据块占用磁盘空间。未下载区域是"hole"。

- `SEEK_DATA`：从偏移量开始查找下一个有数据的位置
- `SEEK_HOLE`：从偏移量开始查找下一个 hole 的位置

这比传统的 `any(buf)` 全零检测更可靠，因为 libtorrent 在 checking_files 期间会临时清零 piece，导致全零检测误判。

### PieceStateTracker

libtorrent 的 `have_piece()` 在 `checking_files` 状态下不可靠。我们维护一个独立的 piece 状态机：

```
NOT_DOWNLOADED → DOWNLOADING → VERIFIED
                                      ↓
                                  CORRUPT → (自动重试)
```

通过 `SEEK_HOLE` 扫描文件系统自举初始状态。

---

## 已解决的重大 Bug（经验教训）

### Safari code=4 / MEDIA_ERR_SRC_NOT_SUPPORTED

**症状：** 视频文件已下载 ~99%，所有 `/stream/` 请求返回 206 + 有效数据，但 Safari 仍报 `code=4`（文件格式不支持或数据损坏）。

**排查过程（历时数轮）：**

```
1. 检查后端响应 → 206 + 有效数据，Content-Range 正确
2. ffprobe 验证文件 → 有效 MP4/H.264，moov 区域完整
3. 模拟 Safari 请求模式拼接 → ffprobe 报 "contradictionary STSC and STCO"
4. 怀疑是 range 重叠/截断导致拼接顺序问题 → 排除
5. 最终发现：libtorrent checking_files 期间会临时清零 piece
   → read_video_range 读到全零数据
   → Safari 的 MP4 demuxer 解析到全零 chunk 后报错 code=4
```

**根本原因：** libtorrent 在 `checking_files` 状态下会临时清零/修改文件中的 piece，导致流式读取到不一致的数据。

**修复：**
- `/api/check/{hash}`：若 torrent 处于 `checking_files`，返回 `head_ready=false`
- `/stream/{hash}`：若 torrent 处于 `checking_files`，返回 503 + `Retry-After: 10`

**教训：**
- 不要假设"文件系统有数据"就等于"数据可安全读取"
-  torrent 客户端的内部状态（checking）会影响文件一致性
- Safari 的媒体解析器对数据一致性要求严格，Chrome 可能更容错

### Range=0-1 误判 hole

MP4 文件开头两个字节是 `00 00`，`not any(data)` 会误判为 hole。修复：用 `SEEK_DATA` 文件系统级检测替代全零检测。

### 内存爆炸（bytes=0-）

`bytes=0-` 请求会让后端读取整个 3.8GB 文件。修复：`MAX_CHUNK = 1MB`，强制截断。

### finished 假状态

libtorrent head+tail 下载完后进入 `finished` 状态，peers 锐减。修复：`seek_priority` 无条件设置 `piece_priority=7` + `deadline=0`，`finished` + `missing` 则 `h.force_recheck()`。

---

## 开发规范

### 代码风格

- 每文件开头 `from __future__ import annotations`
- 类型注解：Python 3.11+ 语法（`str | None`）
- 类名/函数名/变量名：英文
- 注释和 docstring：中文
- f-string 优先；SQL 参数绑定用 `?` 占位符

### 提交规范

- **一个改动一个 commit**
- **commit message 格式：**
  ```
  <type>: <short subject>  (<= 50 chars)

  <long details>  (why + what, 换行 72 chars)
  ```
- 示例：
  ```
  fix: block stream while torrent checking_files

  libtorrent zeros pieces during checking, causing Safari
  MEDIA_ERR_SRC_NOT_SUPPORTED. Return 503 from /stream/ and
  head_ready=false from /api/check/ during checking_files.
  ```

### 文档规范

- 复杂需求：在 `docs/$subject/` 创建 markdown，说明需求、参考、实现、设计、执行计划
- 设计流程用 ASCII graph
- `docs/` 不提交到 git

### 测试与日志

- 测试驱动 & 追溯日志驱动
- 优化问题切换到 bench 模式，以结果决定方向
- 根据 plan 依次实现，一步一个提交

---

## 运维规则

### 服务重启规则

修改 `backend/**/*.py` 后必须重启 `star-archive-backend.service`：

```bash
systemctl restart star-archive-backend
```

### 端口占用

| 端口 | 服务 | 说明 |
|------|------|------|
| 8765 | FastAPI backend | BitTorrent + HTTP API |
| 3000 | Nuxt frontend | SSR 渲染 |
| 443 | Caddy | HTTPS 反向代理 |

---

## 调试技巧

### 检查 torrent 状态

```bash
# 查看日志
tail -f logs/video-stream.log
tail -f logs/stream-router.log
tail -f logs/torrent-engine.log

# 检查文件 sparse hole
python3 -c "
import os
fd = os.open('cache/torrent/.../file.mp4', os.O_RDONLY)
print(os.lseek(fd, 0, os.SEEK_HOLE))
os.close(fd)
"
```

### 模拟 Safari 请求

```bash
curl -r 0-1 -H "Range: bytes=0-1" https://rn.guohuasun.com/stream/HASH
curl -r 0-1048575 -H "Range: bytes=0-3849944386" https://rn.guohuasun.com/stream/HASH
```

---

## 红线

- 绝不外泄隐私数据
- 不运行破坏性命令；`trash` > `rm`
- 不确定时，先问用户
