# UI Design — Star Archive

## Design Philosophy

**Apple-style minimal, light theme, content-first** — aligned with the
[apple-design](https://github.com/emilkowalski/skills) skill (WWDC *Designing
Fluid Interfaces* translated to the web).

- Light background (`#F5F5F7`) with white content surfaces; hierarchy comes
  from whitespace, weight, and proportion — not borders or shadows.
- Every element earns its place: no ambient decoration (no animated blob
  backgrounds, no mouse-follow glows, no gradient buttons).
- One accent color, used sparingly for primary actions and selection states.
- Glassmorphism (`backdrop-filter`) is reserved for floating chrome: sticky
  top bars, cache panel, video modal.

> Historical note: this spec previously described a dark theme. The app has
> since moved to a light theme; this document now matches the code.

---

## Branding

- Logo: winking bunny-eared star ("sexy-cute"), accent gradient `#ff5c7d → #ff375f`, dark-crimson ears, single accent only.
- Source: `frontend/public/logo.svg` (transparent, header/login use);
  `frontend/public/icon.svg` (white rounded-rect, favicon);
  `icon-192x192.png` / `icon-512x512.png` (PWA, rendered from icon.svg via headless Chromium screenshot).
- Header shows logo + wordmark; login page shows the logo at 96px. Do not reintroduce emoji-as-logo or the retired black-square/orange-star icon.

---

## Color System

| Purpose | Hex | Usage |
|---------|-----|-------|
| Page background | `#F5F5F7` | `bg-void` |
| Surface (cards, panels) | `#FFFFFF` | `bg-white` + hairline `border-black/[0.06]` |
| Surface sunken (inputs, chips) | `#F2F2F7` / `#F5F5F7` | |
| Primary text | `#1D1D1F` | `text-foreground` |
| Secondary text | `#86868B` | `text-foreground-muted` |
| **Accent (single)** | `#ff375f` | Play/Add buttons, active ring, liked state, progress |
| Success | `#30d158` | |
| Warning | `#ff9f0a` | |
| Danger | `#ff453a` | |

Rules:

- **Never** reintroduce `#e50914` (Netflix red), rose/violet gradients, or
  per-section accent colors — one accent only.
- Hairline borders `rgba(0,0,0,0.06)`; avoid hard 1px black dividers.

---

## Typography

- System font stack (`-apple-system, SF Pro, Inter, system-ui`); no custom
  webfont. Base body size 17px, line-height 1.5.
- **Size-specific tracking** (never one fixed value): display/heading text
  uses negative tracking (`-0.02em`, via `tracking-tight`), small labels
  (≤12px) keep slightly positive tracking and semibold weight.
- Type scale (px):

| Role | Mobile | Desktop | Weight |
|------|--------|---------|--------|
| Star section header | 24 | 30 | bold, tracking-tight |
| Active title code | 28 | 36 | bold, leading 1.1 |
| Panel titles / nav pills / buttons | 15–17 | | semibold/medium |
| Body / title text | 15 | 17 | regular |
| Meta / dates | 14 | | regular |
| Badges, captions | 10–12 | | bold/semibold — never below 10px |

---

## Spacing & Touch Targets

- Generous whitespace is the primary grouping tool: star sections separated
  by `space-y-16 md:space-y-24`; cards padded `p-5 sm:p-7`.
- Touch targets ≥ 40px: header buttons `h-10`, icon buttons `w-10 h-10`,
  primary buttons `py-2.5` with `px-5/6`, inputs `h-12`.
- Scrollbar-free horizontal rails (`.scrollbar-hide`) with edge fade masks.

---

## Motion (apple-design rules)

- **Response**: feedback on pointer-down, never only on release —
  `active:scale-[0.97]` with a 100ms press curve on every pressable element.
- **Spring easing**: `--ease-spring: cubic-bezier(0.16, 1, 0.3, 1)` for
  enter/hover transitions; `--ease-press` for the 100ms press.
- Animate only `transform` and `opacity`.
- Enter and exit along the same path; panels scale from their trigger.
- **No slow looping ambient animation** (former 20–30s blob drift removed —
  it read as decoration and is near the vestibular-problem frequency band).
- `prefers-reduced-motion`: all motion collapses to short opacity
  cross-fades (global media query in `main.css`).
- `prefers-reduced-transparency`: glass surfaces go near-solid, blur off.

---

## StarCard Layout

Hero cover + info column, with a horizontal thumbnail rail below.

- Hero: natural aspect ratio, never cropped (`w-full h-auto`), width
  `sm:300px / md:360px / lg:440px`, `rounded-2xl`.
- Active title: large code headline (28/36px), title 15/17px, date 14px.
- Actions: Play (solid accent), Copy / Like (hairline outline) — all
  `rounded-full py-2.5 text-[15px]`.
- Thumbnail rail: `w-[120/150/180px]`, `rounded-xl`, active = `ring-2`
  accent at full opacity, inactive = `opacity-70 hover:opacity-100`.

**Image rule unchanged**: preserve original aspect ratio. Never
`object-cover` crop, never stretch.

---

## Glassmorphism Panel Rules

| Element | Treatment |
|---------|-----------|
| Top bar + StarNav | `bg-white/90 backdrop-blur-xl` + hairline bottom border |
| CachePanel | `bg-white/95 backdrop-blur-xl`, `rounded-2xl`, anchored to its toggle button |
| VideoModal | `bg-black/92 backdrop-blur-xl` scrim; controls on gradient overlays |

---

## Interaction Feedback

- Press: `active:scale-[0.97]` (100ms) everywhere; destructive actions keep
  native `confirm()` (used sparingly, only for irreversible deletes).
- Loading: minimal spinner + progress bar (solid accent), no text skeletons
  beyond initial page load.
- Toasts: top-center, `rounded-2xl`, icon + 14px message + 13px detail,
  auto-dismiss 4s, click to dismiss.

See also [Cache Architecture](cache-architecture.md) for cache panel behavior.
