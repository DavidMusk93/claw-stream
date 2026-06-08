# Diff-Sync 设计文档 — 增量actor作品同步

## 背景与问题

当前 `sync_titles.py` 每次同步都是**全量抓取**：

1. **Playwright 浏览器渲染**：每个 star 都要打开 Chromium、加载页面、等待 DOM
2. **全量封面下载**：已有作品的 cover 也会重新下载（DMM CDN / ijavtorrent）
3. **全量数据库 UPSERT**：所有作品（新+旧）都执行 `INSERT ... ON CONFLICT DO UPDATE`

对于 11 个 star、每个 star 约 50-80 个作品：
- Playwright 并发 4 个，单次同步耗时 **30-60 秒**
- 封面下载 400+ 张，耗时 **10-20 秒**
- 数据库写入 400+ 行，耗时 **2-5 秒**

第一性原理：**尽可能快地更新**。全量显然不是答案。

---

## 核心洞察

1. **ijavtorrent actress 页面是服务器端渲染**（SSR）：`curl` 可直接获取完整 HTML，不需要浏览器
2. **页面作品数量有限**：每个 star 约 50-80 个作品，解析极快（selectolax < 10ms）
3. **新增作品极少**：日常同步通常只新增 0-5 个作品
4. **已有作品 cover 不需要更新**：封面图片不会变

---

## Diff-Sync 算法设计

### 阶段 0：预加载本地状态（数据库）

```
SELECT star_id, code, release_date_sort FROM titles
→ 内存 dict: star_id → {code, date}
```

- 单次查询，O(1) 内存查找
- 当前已实现（`load_all_title_codes`）

### 阶段 1：增量页面抓取

**替换 Playwright → 纯 HTTP（HttpxFetcher）**

- HTTP GET 获取 HTML（~200-500KB / page）
- 解析全部作品 code 列表（selectolax，< 10ms）
- 与内存 set 做 diff，只保留**新作品的完整数据**

```python
# 伪代码
html = await httpx_fetcher.fetch(star_page_url)
items = extractor.extract(html)          # 解析全部 ~50 个作品
new_items = [it for it in items if it.code not in existing_codes]
```

**为什么可以解析全部作品？**
- 50 个作品的 HTML 解析 < 10ms，可以忽略
- 真正的瓶颈是网络 I/O 和封面下载
- 不需要"遇到旧作品就停止"的复杂逻辑（页面日期不严格排序）

### 阶段 2：增量封面下载

**只下载新作品的封面**

```python
cover_items = [(it.code, it.cover_url) for it in new_items]
cover_map = await download_covers_batch(cover_items, concurrency=8)
```

- 日常新增 0-5 个作品 → 封面下载从 400+ 降到 0-5 个
- 耗时从 10-20 秒降到 **< 1 秒**

**已有作品 cover 保护**：
- `write_batch` SQL 中：`cover_b64 = CASE WHEN EXCLUDED.cover_b64 != '' THEN EXCLUDED.cover_b64 ELSE titles.cover_b64 END`
- 避免新作品 cover 下载失败时，覆盖已有 cover

### 阶段 3：增量数据库写入

**只 INSERT 新作品，跳过已有作品**

```python
# 旧逻辑：new_items + existing_items 全量 UPSERT
# 新逻辑：只 UPSERT new_items
```

- 日常写入从 400+ 行降到 0-5 行
- 耗时从 2-5 秒降到 **< 50ms**

---

## 性能预期

| 阶段 | 全量同步 | Diff-Sync | 加速比 |
|---|---|---|---|
| 页面抓取 | 30-60s (Playwright) | 2-5s (HTTP) | **10-15x** |
| 封面下载 | 10-20s (400+ covers) | < 1s (0-5 covers) | **20x+** |
| 数据库写入 | 2-5s (400+ rows) | < 50ms (0-5 rows) | **50x+** |
| **总计** | **45-90s** | **3-6s** | **15-20x** |

---

## 边界情况

1. **首次同步（数据库为空）**：`new_items = all_items`，退化为全量同步，但 HTTP 仍比 Playwright 快 10x
2. **大量新增（>20 个）**：`MAX_NEW_TITLES = 20` 限制，避免拖垮
3. **封面下载失败**：SQL `CASE WHEN` 保护已有 cover 不被覆盖
4. **HTTP 被拦截**：如遇到 Cloudflare，可临时切回 Playwright（保留 fallback 能力）

---

## 实现计划

1. `sync_titles.py`：替换 `PlaywrightFetcher` → `HttpxFetcher`
2. `sync_titles.py`：封面下载只传 `new_items`
3. `sinks.py`：`write_batch` SQL 增加 cover_b64 保护
4. 增加 `tests/test_diff_sync.py`：验证 diff 逻辑正确性
5. 增加性能基准：对比 HTTP vs Playwright 抓取耗时

---

## 文档沉淀

本设计文档位于 `docs/star-archive/diff-sync-design.md`。
后续如有变更（如新增数据源、分页处理、反爬策略），同步更新本文档。
