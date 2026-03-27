# Stack Research

**Domain:** Data-dense React dashboard — UI/UX beautification, no CSS framework
**Researched:** 2026-03-27
**Confidence:** HIGH (all versions verified against npm, official docs)

---

## Context

This is a **frontend-only** additive milestone. The existing stack (React 19, Vite, TypeScript, TanStack Query, Lucide, date-fns) is locked. All additions must:
- Work with vanilla CSS custom properties (no Tailwind, no CSS-in-JS)
- Integrate cleanly with React 19
- Be headless or style-agnostic — we own all visual output

---

## Recommended Additions

### Layout Infrastructure

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| react-resizable-panels | ^4.7.6 | Sidebar + main content resizable split layout | Brian Vaughn (React core team). Used by shadcn/ui's resizable primitive. Headless — zero opinion on styling. Supports collapsible panels with `collapsedSize`, drag-to-resize, and persistence via `autoSaveId`. 2.7M weekly npm downloads. |

**Why not a custom flex layout:** Resizable sidebars are finicky — drag handles, collapse thresholds, persistence, keyboard support. `react-resizable-panels` solves all of it in ~3kb. Building it custom wastes a phase.

---

### Table / Data Grid

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| @tanstack/react-table | ^8.21.3 | Headless sortable, filterable table for 600+ comms | From the same TanStack org as React Query already in use. Headless — you render `<table>`, `<tr>`, `<td>` with your own CSS. Multi-sort, column visibility, row expansion, global filter. 15kb. No renderer lock-in. |
| @tanstack/react-virtual | ^3.13.23 | Row virtualization inside the table | Pairs directly with TanStack Table (official guide). Handles 621 rows at 60fps — renders only visible rows. Same API conventions as the rest of TanStack. Headless. |

**Why not react-data-table-component:** Ships opinionated styles that fight custom CSS. Hard to override for dense Linear-style rows. TanStack Table is headless — you get the logic, we write the HTML and CSS.

**Why not Glide Data Grid:** Canvas-based renderer. Impressive performance but completely breaks the DOM — no hover states via CSS, no custom cell components. Wrong tool for a "polish existing app" milestone.

---

### Floating Elements (Tooltips, Popovers)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| @floating-ui/react | ^0.27.19 | Tooltips on table cells, filter popovers, status badges | Industry standard for positioned floating elements post-Popper.js. Handles collision detection, scroll containers, portal rendering. Headless — style with your CSS. React Aria uses it internally. |

**Why not Tippy.js:** Built on Popper.js (deprecated). Floating UI is the modern replacement and is what all major headless libraries (Radix, Headless UI v2) use under the hood.

---

### Animation / Micro-interactions

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| motion | ^12.37.0 | Sidebar collapse transition, view toggle, row expand/collapse | Formerly framer-motion — renamed in mid-2025 when it went independent. Import path is now `motion/react`. Same API, same ecosystem, 30M monthly npm downloads. Hardware-accelerated via Web Animations API with JS fallback for spring physics. Surgical use only — layout transitions, not decorative animations. |

**Why not CSS transitions only:** CSS transitions can't animate `height: auto` (sidebar collapse), can't sequence animations, and can't share exit animations with React component unmounting. Motion's `AnimatePresence` handles all three cleanly.

**Why not React Spring:** Heavier API, less community adoption in 2025. Motion (framer-motion) is the clear ecosystem standard.

**Scope constraint:** Use motion only for:
1. Sidebar collapse/expand
2. View toggle (table ↔ card) crossfade
3. Row expand/collapse in card view

Do NOT use for page transitions, loading states, or decorative flourishes. Keeps bundle impact minimal.

---

### Persistence (No New Library)

View toggle (table/card) and sidebar collapse state should persist in `localStorage` via a custom `useLocalStorage` hook. No library needed — the pattern is a ~15-line hook. Using a library (like `use-local-storage-state`) for this adds a dependency for what is trivially implementable. React 19's `useSyncExternalStore` is the right primitive if cross-tab sync is ever needed.

---

### Responsive Design (No New Library)

CSS container queries are production-ready in all major browsers as of 2024 and are the correct tool for component-level responsiveness in a component-driven app. Use them alongside viewport media queries:

- **Media queries** → macro layout (1-column vs sidebar+main)
- **Container queries** → component-level adaptation (table column hiding, card layout reflow)

No new library. Write `@container` rules in the same inline style blocks already used.

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| MUI / Ant Design / Chakra | Ships full design systems that override CSS custom properties. Fighting their styles wastes the whole milestone. | Headless libraries + own CSS |
| react-data-table-component | Opinionated styles, difficult dark theme customization, not headless | @tanstack/react-table |
| Tailwind CSS | Explicitly out of scope per PROJECT.md constraints | CSS custom properties (already in use) |
| Glide Data Grid / AG Grid | Canvas-based or enterprise-priced. No CSS access for styling cells. | @tanstack/react-table + CSS |
| react-spring | Heavier than Motion, smaller ecosystem in 2025 | motion (^12.x) |
| framer-motion (old package) | Deprecated — active development moved to `motion` package | `motion` from `motion/react` |
| Tippy.js | Built on deprecated Popper.js | @floating-ui/react |
| react-use / ahooks | Large utility libraries where we need ~2 hooks | Write the hooks inline (useLocalStorage, useDebounce) |

---

## Installation

```bash
# Layout and table (core additions)
npm install react-resizable-panels @tanstack/react-table @tanstack/react-virtual

# Floating UI (tooltips, popovers)
npm install @floating-ui/react

# Animation (motion — formerly framer-motion)
npm install motion
```

---

## Alternatives Considered

| Category | Recommended | Alternative | When to Use Alternative |
|----------|-------------|-------------|-------------------------|
| Table | @tanstack/react-table | react-data-table-component | If you want batteries-included with acceptable default styles and don't care about visual control |
| Layout | react-resizable-panels | CSS Grid + custom JS | If you never need user-resizable panels — a fixed sidebar is fine with pure CSS |
| Animation | motion | CSS transitions only | If animations scope stays trivial (no height:auto, no exit animations) |
| Floating | @floating-ui/react | Native `<details>`/`title` attr | For extremely simple tooltips only — not for data-dense dashboard popovers |

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| @tanstack/react-table ^8.21.3 | React 19 | Confirmed React 19 compatible. Peer dep is react >=16.8. |
| @tanstack/react-virtual ^3.13.23 | @tanstack/react-table ^8.x | Use together via the official virtualized rows example in TanStack docs |
| react-resizable-panels ^4.7.6 | React 19 | Active development, last publish <24 hours ago as of research date |
| motion ^12.37.0 | React 19 | Renamed from framer-motion in mid-2025. Import: `import { motion, AnimatePresence } from "motion/react"` |
| @floating-ui/react ^0.27.19 | React 19 | Actively maintained, last publish ~24 days ago |

---

## Stack Patterns by Variant

**For the sidebar:**
- Use `react-resizable-panels` with `PanelGroup direction="horizontal"` + `Panel collapsible collapsedSize={0}` + `PanelResizeHandle`
- Wrap collapse toggle in `motion` `AnimatePresence` for smooth width transition
- Persist collapse state via `autoSaveId` prop (built-in to the library)

**For the communications table:**
- Use `@tanstack/react-table` for sort/filter/expand logic
- Pair with `@tanstack/react-virtual` for row virtualization (621 comms = needs it)
- Render plain `<table>` with CSS custom properties — no library CSS import

**For responsive behavior:**
- Sidebar auto-collapses at viewport < 768px via a `useEffect` + `ResizeObserver` on `PanelGroup`
- Table hides low-priority columns on mobile via `column.toggleVisibility()` from TanStack Table

---

## Sources

- [@tanstack/react-table on npm](https://www.npmjs.com/package/@tanstack/react-table) — version 8.21.3 confirmed
- [@tanstack/react-virtual on npm](https://www.npmjs.com/package/@tanstack/react-virtual) — version 3.13.23 confirmed (last publish 11 days ago)
- [react-resizable-panels on npm](https://www.npmjs.com/package/react-resizable-panels) — version 4.7.6 confirmed (last publish <24 hours ago)
- [motion on npm](https://www.npmjs.com/package/motion) — version 12.37.0 confirmed, framer-motion renamed mid-2025
- [@floating-ui/react on npm](https://www.npmjs.com/package/@floating-ui/react) — version 0.27.19 confirmed
- [TanStack Table Virtualization Guide](https://tanstack.com/table/v8/docs/guide/virtualization) — official guidance on react-virtual pairing
- [Motion upgrade guide](https://motion.dev/docs/react-upgrade-guide) — framer-motion → motion migration
- [CSS Container Queries 2026 — LogRocket](https://blog.logrocket.com/container-queries-2026/) — production-ready status confirmed

---

*Stack research for: NHTSA Comms Tracker — UI/UX Beautification milestone*
*Researched: 2026-03-27*
