# HTML Redesign Log — Netflix Dark Immersive

## 功能审计清单（重构前必须保留）

### 数据层
- [x] 读取 config.json actress 列表
- [x] 读取 /tmp/actress-b64 base64 封面
- [x] 读取 /tmp/actress-news ijavtorrent 作品数据
- [x] 读取 /tmp/actress-jable jable 数据（m3u8 + 封面）
- [x] 保存 base64 图片到 images/ 目录
- [x] 按日期降序排序作品，取前 3
- [x] actress 按最新作品日期排序

### UI 功能
- [x] 暗色/亮色主题适配（prefers-color-scheme）
- [x] 搜索过滤（名字、日文名、番号）
- [x] 导航高亮（Intersection Observer）
- [x] 返回顶部按钮
- [x] Toast 提示
- [x] 弹窗视频播放器
- [x] 键盘快捷键（←→ 10s seek，ESC 关闭）
- [x] 缓存管理面板（折叠、列表、删除、清理全部）
- [x] 缓存状态徽章（已缓存/下载中/未缓存）
- [x] 复制磁力链接
- [x] 播放按钮（优先本地缓存，否则启动下载）
- [x] 预缓存策略（页面加载后自动添加最新 13 个 magnet）
- [x] 状态轮询（缓冲时显示速率和进度）
- [x] seeking 时启动状态轮询
- [x] 播放器 30s 超时

### 新增功能
- [x] 全局 id 分配（1-39）
- [x] 作品卡片显示 id badge
- [x] 预缓存目标标记（id 在 {1+3*n}）金色边框
- [x] 缓存面板显示 av_card.id
- [x] Netflix 暗色沉浸式风格
- [x] 英雄横幅区
- [x] 横向滚动作品带
- [x] 卡片悬停动效
- [x] 玻璃态播放器弹窗

## 开发日志

### Step 1: 功能审计
- 读取原 generate-report.js（1091 行）
- 确认数据流：config -> solo actresses -> ijav works -> jable works -> sort -> HTML
- 确认交互：search, nav highlight, cache panel, video modal, playback, prefetch

### Step 2: 数据处理增强
- 在 actressData.forEach 之前加入全局 id 分配：`globalIdMap[code] = gid++`
- id 从 1 开始，每个 actress 的 3 个作品顺序分配
- 预缓存目标检测：`globalId % 3 === 1` -> `prefetch-target` CSS 类

### Step 3: HTML/CSS/JS 重构
- 重写 CSS：Netflix 暗色风格（#0a0a0a 背景、#141414 卡片、e50914 红色强调）
- 重写 HTML body：
  - 固定顶部导航栏（glassmorphism）
  - 可折叠搜索栏
  - 英雄横幅（第一个 actress）
  - 统计条（女优数/作品数）
  - 13 个横向滚动作品带
  - 底部缓存管理面板
- 重写 JS：保留所有原有交互，优化视觉效果
  - 搜索过滤 + 导航联动
  - 缓存管理（完整保留）
  - 视频播放（完整保留）
  - 键盘快捷键（完整保留）
  - 预缓存策略（完整保留）

### Step 4: 验证结果
```
File size: 234727 chars
Actor rows: 13
AV cards: 39
Prefetch targets: 13 (expected 13)
Cache badges: 39 (expected 39)
Play buttons: 39 (expected 39)
Global IDs: 1-39, all unique
Hero banner: Yes
Stats bar: Yes
Top nav: Yes
Search bar: Yes
```

### 设计决策
- 移除封面切换功能（点击作品切换 actress 大图）-> 在新设计中不再需要，因为每个作品已经有独立封面
- 导航从底部改为顶部固定（更符合 Netflix 风格）
- 搜索从常驻改为可折叠（节省空间）
- 缓存徽章从文字改为彩色圆点（更精致）
- 缓存面板显示 av_card.id（如 #1, #4）
