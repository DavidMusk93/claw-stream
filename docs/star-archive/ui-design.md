# UI 设计理念 — Star Archive

## 1. 设计哲学

**Apple-style minimal + dark mode**

- 纯黑背景 (`#000000` / `bg-black`)，让图片内容本身成为视觉焦点。
- 无多余装饰线、无大面积 card 阴影，靠留白和比例建立层级。
- 玻璃态 (glassmorphism) 仅用于浮动面板（缓存管理、弹窗），主内容区保持 flat。

## 2. 色彩系统

| 用途 | 色值 | Tailwind |
|------|------|----------|
| 页面背景 | `#000000` | `bg-black` |
| 主文字 | `#ffffff` | `text-white` |
| 次要文字 | `#8e8e93` | `text-[#8e8e93]` |
| 强调色（播放、激活） | `#ffffff` | `bg-white text-black` |
| 成功 | `#30d158` | — |
| 危险 | `#ff453a` | — |
| 面板背景 | `#1c1c1e` | `bg-[#1c1c1e]` |

## 3. 字体

- 主字体：`Inter` (Google Fonts)，用于所有 UI 文字。
- 衬线装饰：`Playfair Display`，目前未大面积使用，预留用于标题装饰。
- 字号层级：以 13px / 14px / 15px / 17px / 26px 递进，无夸张大字。

## 4. 图片展示原则

**保持原始比例，绝不裁剪或拉伸。**

- 大图封面：`w-full h-auto block`，按容器宽度自然缩放。
- 缩略图 dock：`object-contain`，黑底自然露出，不裁切封面内容。
- 禁止 `object-cover`（会裁切边缘）和 `object-fit: fill`（会变形）。

## 5. StarCard 布局规范

StarCard 是核心展示单元，采用**大图 + 缩略图选择器**模式。

### Desktop (≥640px)

```
┌────────────────────────────┬───────────┐
│                            │   [t1]    │
│      Hero Image            │  2:3      │
│      (aspect natural)      ├───────────┤
│                            │   [t2]    │
│      高度 = H              │  2:3      │
│                            ├───────────┤
│                            │   [t3]    │
│                            │  2:3      │
└────────────────────────────┴───────────┘
              ↑                  ↑
       大图高度 H        =  dock 总高度
```

- 左侧：大图 flex-1，下方紧跟播放按钮和作品信息。
- 右侧：dock 竖排 `flex-col`，总高度通过 `ResizeObserver` 锁定为**大图图片实际渲染高度**。
- dock 内每张缩略图高度均分：`(H - gaps) / N`，宽度按原图比例自然撑出。
- 每个 star 固定展示 **3 部最新作品**。

### Mobile (<640px)

```
┌────────────────────────────┐
│                            │
│      Hero Image            │
│      (aspect natural)      │
│      宽度 = W              │
│                            │
└────────────────────────────┘
┌──────────┬──────────┬──────────┐
│   [t1]   │   [t2]   │   [t3]   │
│   2:3    │   2:3    │   2:3    │
└──────────┴──────────┴──────────┘
    ↑                              ↑
 dock 总宽度 = W
```

- 大图在上，dock 在下横排 `flex-row`。
- dock 总宽度通过 `ResizeObserver` 锁定为**大图图片实际渲染宽度**。
- dock 内每张缩略图宽度均分：`(W - gaps) / N`，高度按原图比例自然撑出。

### 动态尺寸计算

```javascript
// Desktop
dockHeight = heroImageHeight
thumbHeight = (dockHeight - (N - 1) * gap) / N

// Mobile
dockWidth = heroImageWidth
thumbWidth = (dockWidth - (N - 1) * gap) / N
```

实现方式：
1. `ref` 获取大图 `<img>` 和容器。
2. `@load` + `ResizeObserver` 监听实际渲染尺寸。
3. 用 `computed` 动态生成 dock 和每张缩略图的 `style` 绑定。
4. 切换 active title 时重新测量（不同封面可能尺寸略有差异）。

## 6. 玻璃态面板规范

仅用于浮动元素：

| 元素 | 背景 | 边框 | 阴影 |
|------|------|------|------|
| CachePanel | `glass-strong` | `border-glass-border` | `shadow-glass` |
| VideoModal | `bg-black/90` | — | — |
| 顶部导航 | `bg-black/90 backdrop-blur-xl` | — | — |

## 7. 交互反馈

- 按钮：`active:scale-95` 或 `hover:opacity-90`，无生硬边框变化。
- 缩略图选中：`ring-2 ring-white opacity-100`，未选中 `opacity-40 hover:opacity-70`。
- 加载状态：极简 spinner，无文字骨架屏。
