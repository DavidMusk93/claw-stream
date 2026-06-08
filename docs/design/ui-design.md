# UI Design — Star Archive

## Design Philosophy

**Apple-style minimal + dark mode**

- Pure black background (`#000000` / `bg-black`) makes image content the visual focus.
- No decorative lines or large card shadows; hierarchy comes from whitespace and proportion.
- Glassmorphism is reserved for floating panels (cache management, modals). The main content area stays flat.

---

## Color System

| Purpose | Hex | Tailwind |
|---------|-----|----------|
| Page background | `#000000` | `bg-black` |
| Primary text | `#ffffff` | `text-white` |
| Secondary text | `#8e8e93` | `text-[#8e8e93]` |
| Accent (play, active) | `#ffffff` | `bg-white text-black` |
| Success | `#30d158` | — |
| Danger | `#ff453a` | — |
| Panel background | `#1c1c1e` | `bg-[#1c1c1e]` |

---

## Typography

- Primary font: `Inter` (Google Fonts) for all UI text.
- Serif accent: `Playfair Display`, reserved for decorative titles (not yet widely used).
- Type scale: 13 px / 14 px / 15 px / 17 px / 26 px; no oversized headlines.

---

## Image Display Rules

**Preserve original aspect ratio. Never crop or stretch.**

- Hero covers: `w-full h-auto block`, scaling naturally to container width.
- Thumbnail dock: `object-contain`, revealing the black background naturally without cropping cover content.
- Prohibit `object-cover` (crops edges) and `object-fit: fill` (distorts).

---

## StarCard Layout

StarCard is the core display unit. It uses a **hero image + thumbnail selector** pattern.

### Desktop (≥640 px)

```
┌────────────────────────────┬───────────┐
│                            │   [t1]    │
│      Hero Image            │  2:3      │
│      (aspect natural)      ├───────────┤
│                            │   [t2]    │
│      height = H            │  2:3      │
│                            ├───────────┤
│                            │   [t3]    │
│                            │  2:3      │
└────────────────────────────┴───────────┘
              ↑                  ↑
       hero height H    =  dock total height
```

- Left: hero image with `flex-1`, followed by the play button and title info.
- Right: vertical `flex-col` dock. Total height is locked to the **actual rendered height of the hero image** via `ResizeObserver`.
- Each thumbnail inside the dock shares equal height: `(H - gaps) / N`. Width follows the original aspect ratio.
- Each star shows **3 latest titles** fixed.

### Mobile (<640 px)

```
┌────────────────────────────┐
│                            │
│      Hero Image            │
│      (aspect natural)      │
│      width = W             │
│                            │
└────────────────────────────┘
┌──────────┬──────────┬──────────┐
│   [t1]   │   [t2]   │   [t3]   │
│   2:3    │   2:3    │   2:3    │
└──────────┴──────────┴──────────┘
    ↑                              ↑
 dock total width = W
```

- Hero image on top, dock below in horizontal `flex-row`.
- Dock total width is locked to the **actual rendered width of the hero image** via `ResizeObserver`.
- Each thumbnail shares equal width: `(W - gaps) / N`. Height follows the original aspect ratio.

### Dynamic Size Calculation

```javascript
// Desktop
dockHeight = heroImageHeight
thumbHeight = (dockHeight - (N - 1) * gap) / N

// Mobile
dockWidth = heroImageWidth
thumbWidth = (dockWidth - (N - 1) * gap) / N
```

Implementation:
1. Use `ref` to access the hero `<img>` and its container.
2. Listen to actual rendered dimensions with `@load` + `ResizeObserver`.
3. Generate dock and thumbnail `style` bindings dynamically with `computed`.
4. Re-measure when the active title changes (different covers may vary slightly in size).

---

## Glassmorphism Panel Rules

Reserved for floating elements only:

| Element | Background | Border | Shadow |
|---------|------------|--------|--------|
| CachePanel | `glass-strong` | `border-glass-border` | `shadow-glass` |
| VideoModal | `bg-black/90` | — | — |
| Top navigation | `bg-black/90 backdrop-blur-xl` | — | — |

---

## Interaction Feedback

- Buttons: `active:scale-95` or `hover:opacity-90`; no harsh border changes.
- Thumbnail selected: `ring-2 ring-white opacity-100`; unselected: `opacity-40 hover:opacity-70`.
- Loading state: minimal spinner, no text skeletons.

See also [Cache Architecture](cache-architecture.md) for cache panel behavior.
