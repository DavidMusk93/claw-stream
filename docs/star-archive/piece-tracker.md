# PieceStateTracker — 位图状态机

> 适用：`backend/services/piece_tracker.py`
> 目标：O(1) piece 状态查询，替代 Python 级循环

---

## 核心设计：Python `int` 位图

3 个 `int` 编码 4 个状态：

```python
_verified    = 0  # bit p = 1 → VERIFIED
_corrupt     = 0  # bit p = 1 → CORRUPT
_downloading = 0  # bit p = 1 → DOWNLOADING
# all 0 → NOT_DOWNLOADED
```

所有位运算（`& | ~ ^ << >> .bit_count()`）在 C 层执行，由 `longobject.c` 实现。
POPCNT（`.bit_count()`）直接映射到单条 `POPCNT` CPU 指令。

---

## 状态机

```
NOT_DOWNLOADED ──► DOWNLOADING (request_pieces 设置 priority)
       │                  │
       │                  ▼ piece_finished_alert
       │            VERIFIED
       │                  │
       │                  ▼ hash_failed_alert
       │            CORRUPT ──► (自动重试 → DOWNLOADING)
       │
       └──► _bootstrap_from_filesystem (SEEK_HOLE 扫描)
```

---

## O(1) 查询

### head_ready()

```python
def head_ready(self) -> bool:
    return self._moov_vc == self._moov_pc   # 一条整数比较
```

预计算 moov mask：
```python
def set_moov_range(self, moov_start, moov_end):
    sp = (self.file_offset + moov_start) // self.piece_length
    ep = (self.file_offset + moov_end) // self.piece_length
    self._moov_mask = ((1 << (ep - sp + 1)) - 1) << sp
    self._moov_pc = ep - sp + 1
    self._moov_vc = (self._verified & self._moov_mask).bit_count()
```

`_moov_vc` 增量维护：
- `_set_verified(p)` → 若 p 在 moov 内，`+= 1`
- `_set_corrupt(p)` → 若 p 在 moov 内，`-= 1`

### verified_count()

```python
def verified_count(self) -> int:
    return self._verified.bit_count()   # POPCNT 指令
```

---

## 批量请求

```python
def request_pieces(self, start_piece, end_piece):
    mask = ((1 << (end - start + 1)) - 1) << start
    unavailable = (self._verified | self._downloading) & mask
    need = (mask & ~unavailable) >> start

    # 批量设置 priority=7 + deadline=0
    pieces_to_set = [p for p in range(start, end+1) if need & (1 << (p-start))]
    prios = list(self.handle.piece_priorities())
    for p in pieces_to_set:
        self.handle.set_piece_deadline(p, 0)
        prios[p] = 7
    self.handle.prioritize_pieces(prios)
```

**关键**：使用 `prioritize_pieces(list)` 批量 API，不是 `piece_priority(p, 7)`。
后者在 Python bindings 中常被**静默忽略**。

---

## Bootstrap (SEEK_HOLE)

```python
def _bootstrap_from_filesystem(self):
    fd = os.open(self.path, os.O_RDONLY)
    offset = self.file_offset
    while offset < file_end:
        piece = offset // piece_len
        piece_end = min((piece + 1) * piece_len, file_end)
        offset_in_file = offset - self.file_offset   # 视频文件相对偏移！
        hole = os.lseek(fd, offset_in_file, os.SEEK_HOLE)
        if hole >= piece_end_in_file:
            self._verified |= (1 << piece)
        offset = piece_end
```

**关键**：`lseek` 偏移必须是**视频文件相对偏移**，不是 torrent 绝对偏移。
ABF-328 的 `file_offset=2,001,226`，旧代码用绝对偏移导致扫描失败。

---

## 严格同步 (strict=True)

```python
def _overlay_have_piece(self, strict=False):
    for p in range(self.start_piece, self.end_piece + 1):
        if self.handle.have_piece(p):
            if not (self._verified & (1 << p)):
                if strict:
                    continue   # 跳过未 bootstrap 确认的
                self._set_verified(p)
        elif strict and (self._verified & (1 << p)):
            # recheck 完成后 have_piece=false → 清除 VERIFIED
            self._verified &= ~(1 << p)
```

- `strict=False`（增量）：只覆盖 NOT_DOWNLOADED，保护已有状态
- `strict=True`（双向）：不信任未 bootstrap 的 have_piece，防止 page-cache 误报

---

## 性能对比

| 操作 | 旧 (list 循环) | 新 (int 位图) | 加速 |
|------|---------------|---------------|------|
| head_ready (5 pieces) | 1378 ns | 111 ns | **12.4×** |
| verified_count (5000 pieces) | 210271 ns | 456 ns | **461×** |
| moov 扫描 (每 req) | 10.9 ms | 0.026 ms | **829×** |
