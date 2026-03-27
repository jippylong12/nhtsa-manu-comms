# Project Research Summary

**Project:** NHTSA Comms Tracker — UI/UX Beautification Milestone
**Domain:** Data-dense React dashboard — sidebar navigation, table views, design system migration
**Researched:** 2026-03-27
**Confidence:** HIGH

## Executive Summary

This is a frontend-only visual overhaul of an existing React 19 / Vite / TypeScript app that tracks NHTSA manufacturer communications per vehicle. The existing data layer (TanStack Query, backend API, SSE fetch progress) is complete and locked. The goal is to transform a functional-but-plain single-page CRUD interface into a professional-grade data tool matching the visual quality and interaction patterns of Linear and GitHub Issues. The recommended approach centers on four additions to the existing stack — `react-resizable-panels`, `@tanstack/react-table` + `@tanstack/react-virtual`, `@floating-ui/react`, and `motion` — all headless and fully compatible with the existing vanilla CSS custom property system.

The critical architectural constraint is that `App.tsx` is a 658-line God component and the app has no root layout shell. Every planned feature (sidebar, table view, cross-vehicle dashboard, responsive layout) requires a CSS Grid layout shell to exist first. The app layout restructure is the true Phase 1 — it is not optional and it is not parallelizable. All other components slot into the grid shell after it exists. The second structural prerequisite is a design token audit: the inline `<style>` blocks in components must be migrated to co-located `.css` files using existing tokens, not ad-hoc hex values.

The primary risks are scroll model breakage when the sidebar is introduced (solved by CSS Grid root layout from the start), render performance on vehicles with 600+ communications (solved by `@tanstack/react-virtual` built into the table from day one, not retrofitted), and prop drilling explosion when splitting the God component (solved by mapping state ownership before writing a single new component). All three risks have clear, low-cost prevention strategies — the danger is skipping the foundation work and discovering these problems mid-milestone.

---

## Key Findings

### Recommended Stack

The existing stack (React 19, Vite, TypeScript, TanStack Query, Lucide, date-fns) is locked. Five targeted additions cover the full feature set with no CSS framework and no design system lock-in. All packages are headless — they provide logic and you control the HTML and CSS.

**Core technologies:**
- `react-resizable-panels ^4.7.6`: Sidebar layout with drag-to-resize and collapse — Brian Vaughn (React core team), 2.7M weekly downloads, built-in `autoSaveId` persistence. Zero styling opinions.
- `@tanstack/react-table ^8.21.3`: Headless sortable/filterable table for 600+ comms — same TanStack org as existing React Query. You render `<table>`, you write the CSS.
- `@tanstack/react-virtual ^3.13.23`: Row virtualization paired directly with TanStack Table — renders only visible rows, constant DOM count regardless of dataset size.
- `@floating-ui/react ^0.27.19`: Tooltips, filter popovers, and positioned elements — industry standard post-Popper.js. Headless, used internally by Radix and Headless UI v2.
- `motion ^12.37.0`: Sidebar collapse/expand, view toggle crossfade, row expand animations — formerly framer-motion (renamed mid-2025). Import path is `motion/react`. Scope-limited to 3 use cases only.
- Two custom hooks (`useLocalStorage`, `useDebounce`) replace any utility library — ~15 lines each, no new dependency.
- CSS container queries (no library) handle component-level responsive behavior alongside viewport media queries.

See `.planning/research/STACK.md` for full version compatibility matrix and installation commands.

### Expected Features

The communications view has 88–621 items per vehicle. Users are in operational mode — scanning, filtering, identifying priority items. Every feature decision should serve that workflow.

**Must have (table stakes) — v1:**
- App layout restructure (sidebar shell + CSS Grid root) — prerequisite for all other features
- Persistent sidebar with vehicle list — professionals expect visible navigation; click-away-to-navigate breaks flow
- Table view with sortable columns + sticky header — essential scanning mode for 100+ items
- View toggle (table / card) with localStorage persistence — different users have different scanning preferences
- Sticky filter/search bar — users scrolling 600+ comms need context at all times
- Active filter chips with clear-all — hidden filter state is a trust-breaker
- Inline detail drawer (right-side slide-in panel) — replaces full-page navigation; keeps list context
- Cross-vehicle overview dashboard — aggregate stats as landing screen when no vehicle selected
- Responsive layout (breakpoints at 768px and 480px; card-only on mobile)
- Improved skeleton loaders (must match actual content shape)
- Three empty state variants: no vehicle, no data fetched, no filter match

**Should have (competitive) — v1.x after validation:**
- Display density toggle (compact/default/spacious row heights via `--row-height` CSS custom property)
- Column visibility toggle (popover checklist, per-vehicle persistence)
- Priority-grouped view (collapsible sections by High/Medium/Low)
- Search result highlight (`<mark>` tags on matched text)
- Vehicle fetch status indicator (relative date + color-coded staleness in sidebar)

**Defer (v2+):**
- Saved filter presets — requires backend persistence; architecture should not block it
- Full keyboard shortcut system — explicitly out of scope per PROJECT.md
- Multi-vehicle comparison view — requires new data architecture

See `.planning/research/FEATURES.md` for full prioritization matrix and dependency graph.

### Architecture Approach

The app requires a two-phase structural change before new features are added. Phase 1 is establishing the CSS Grid AppShell (`grid-template-columns: [sidebar-width] 1fr; height: 100vh`) with independently scrollable sidebar and main content areas. Phase 2 is mapping state ownership from the God component before splitting it: `selectedVehicleId` lives in App-level state (max 2 levels of prop drilling), filter state lives in the Dashboard view (not in sub-components), and UI preferences (view toggle, density) live in the `useViewPreference` hook backed by localStorage. Feature hooks are not touched in this milestone — only components change.

**Major components:**
1. `AppShell` — CSS Grid layout shell (sidebar + main), responsive class toggling, no business logic
2. `Sidebar` + `SidebarVehicleItem` — vehicle list, active selection, fetch status indicator; calls `useVehicles` directly
3. `CommunicationTable` — headless TanStack Table with react-virtual row virtualization, sticky header, sort state
4. `CommunicationCardGrid` — improved card view replacing the card path in current `CommunicationList`
5. `Toolbar` — search input, filter chips, view toggle assembled; receives filter state + setters from Dashboard as props
6. `OverviewDashboard` — cross-vehicle aggregate stats landing screen (new)
7. Primitive components (`Button`, `Badge`, `Input`, `Skeleton`, `Tooltip`) — wrap existing CSS classes with typed React props

**Build order (no blocked work):**
CSS extraction → primitives → AppShell + Sidebar → ViewToggle hook → CommunicationTable → Toolbar refactor → StatsBar → OverviewDashboard → CommunicationCardGrid → PageHeader

See `.planning/research/ARCHITECTURE.md` for full component interface specs, data flow diagrams, and design system extraction plan.

### Critical Pitfalls

1. **Sidebar breaks scroll model** — Introducing a sidebar into the existing single-scroll DOM without restructuring the root layout first causes the sidebar to scroll away with content or clip the main area. Prevention: establish `height: 100vh; overflow: hidden` CSS Grid root in Phase 1, before writing any feature component. Recovery cost is HIGH if skipped.

2. **CSS migration creates two token sources** — Extracting `<style>` blocks to `.css` files component-by-component without a prior token audit produces hardcoded hex values in component CSS that diverge from `index.css`. Prevention: audit all tokens first, define the complete token set, then migrate. Grep verification: `grep -r '#[0-9a-fA-F]{3,6}' frontend/src/` should return zero results outside `index.css` after migration.

3. **Table renders 600+ rows without virtualization** — `perPage: 100` is an invisible ceiling, not a UX decision. Removing it without `@tanstack/react-virtual` produces visible jank at 200+ rows. Prevention: virtualization is a design constraint going in, not a performance fix added later. Never retrofit.

4. **God component split creates prop drilling chains** — `App.tsx` has 658 lines and owns all state. Splitting structurally (moving JSX to files) without mapping state ownership first results in the same component spread across files with 4-5 levels of prop drilling. Prevention: define state ownership boundaries before writing any new component; `selectedVehicleId` must not pass through more than 2 levels.

5. **Responsive design added as afterthought** — Dense tables are the hardest pattern to make responsive. `overflow-x: auto` on the table container creates a horizontal scroll trap on touch devices. Prevention: define mobile behavior (table collapses to card layout below 768px) at table component spec time, not in a follow-up pass.

See `.planning/research/PITFALLS.md` for UX pitfalls, performance traps, "looks done but isn't" checklist, and recovery strategies.

---

## Implications for Roadmap

Based on research, the dependency chain is clear: layout shell must precede everything, table view is the centerpiece feature, and polish/extras come last. Suggest 4 phases.

### Phase 1: Foundation — Layout Shell + Design System

**Rationale:** Every other feature in this milestone depends on the CSS Grid root existing first (scroll model, sidebar, responsive breakpoints). The token audit must happen before any CSS migration begins or you create two sources of truth. God component state mapping must happen before splitting App.tsx or you create prop drilling chains. All three critical pitfalls are addressed here before they can cause damage.

**Delivers:** AppShell with sidebar, CSS Grid root layout, complete token system in `index.css`, `<style>` tags extracted to co-located `.css` files, primitive components (`Button`, `Badge`, `Input`, `Skeleton`), `useViewPreference` hook, state ownership map (which state goes where).

**Features addressed:** App layout restructure, sidebar with vehicle list, view toggle + persistence infrastructure

**Avoids:** Pitfall 1 (scroll model), Pitfall 2 (token divergence), Pitfall 4 (God component split), Pitfall 5 (responsive afterthought — breakpoints defined here)

**Research flag:** Standard patterns — CSS Grid layout shell is well-documented; no deeper research needed.

---

### Phase 2: Communications Views — Table + Card

**Rationale:** The table view is the highest-value feature and the most complex component. It slots into the AppShell established in Phase 1. Virtualization is built in from the start (not retrofitted). The card view is an improved variant of the existing card rendering. Responsive behavior (table collapses to card on mobile) is designed in, not added later.

**Delivers:** `CommunicationTable` with TanStack Table + react-virtual, sortable columns, sticky header, column visibility toggle (P2), inline detail drawer (right-side slide-in panel), improved `CommunicationCardGrid`, active filter chips, sticky filter bar, responsive breakpoint behavior (card-only on mobile), improved empty states (3 variants), improved skeleton loaders.

**Stack used:** `@tanstack/react-table ^8.21.3`, `@tanstack/react-virtual ^3.13.23`, `@floating-ui/react ^0.27.19` (detail drawer tooltips/popovers), `motion` (detail drawer slide-in animation)

**Features addressed:** Table view, inline detail drawer, active filter chips, sticky filter bar, responsive layout, empty states, skeleton loaders

**Avoids:** Pitfall 3 (600+ row virtualization built in), Pitfall 5 (mobile card fallback designed in)

**Research flag:** Standard patterns — TanStack Table + Virtual are well-documented with official virtualized rows example. No deeper research needed.

---

### Phase 3: Navigation + Overview

**Rationale:** The cross-vehicle dashboard and sidebar status indicators depend on the AppShell (Phase 1) and the communications view patterns (Phase 2) being established first. This phase wires navigation, URL state for shareable vehicle links, and the overview dashboard.

**Delivers:** `OverviewDashboard` (cross-vehicle aggregate stats with clickable drill-down), vehicle fetch status indicator in sidebar (relative date + staleness color), URL search params for selected vehicle state (`?vehicleId=3`), `motion` sidebar collapse/expand animation, `react-resizable-panels` drag-to-resize sidebar.

**Stack used:** `react-resizable-panels ^4.7.6`, `motion ^12.37.0` (sidebar collapse animation)

**Features addressed:** Cross-vehicle dashboard, vehicle fetch status indicator, sidebar collapse/expand, URL-based vehicle selection

**Avoids:** UX pitfall — sidebar selection state not reflected in URL (currently identified as a pitfall in research)

**Research flag:** Standard patterns — react-resizable-panels API is straightforward and well-documented. URL search params are native browser API. No research needed.

---

### Phase 4: Polish + P2 Features

**Rationale:** Once the core loop (sidebar → table/card view → detail drawer) is shipping and validated, add the differentiating polish features. These are all low-to-medium complexity and can be done in any order.

**Delivers:** Display density toggle (compact/default/spacious via `--row-height`), priority-grouped view (collapsible sections), search result highlight (`<mark>` tags), view toggle crossfade animation (`motion`), row expand/collapse animation.

**Stack used:** `motion ^12.37.0` (view toggle crossfade, row expand)

**Features addressed:** Display density toggle, priority-grouped view, search highlight, remaining motion animations

**Avoids:** Scope creep — saved filter presets, full keyboard shortcut system, drag-to-reorder vehicles, infinite scroll are explicitly deferred

**Research flag:** Standard patterns — no research needed. These are well-understood UI patterns.

---

### Phase Ordering Rationale

- **Phase 1 must be first.** Layout shell and token system are structural prerequisites with HIGH recovery cost if skipped. This is not negotiable per pitfalls research.
- **Phase 2 before Phase 3** because the detail drawer and filter patterns are needed before the overview dashboard can link into them. The overview's "clickable drill-down into vehicle comms" requires the comms view to exist.
- **Phase 4 is independent** of Phase 3 but benefits from the full app being assembled first so polish work can be verified in context.
- The build order within Phase 2 follows the ARCHITECTURE.md component dependency sequence: CSS extraction → primitives → AppShell → table → toolbar → card → page header.

### Research Flags

Phases with standard patterns (skip `/gsd:research-phase`):
- **Phase 1:** CSS Grid layout shell, token extraction, primitive components — all well-documented, established patterns.
- **Phase 2:** TanStack Table + Virtual — official documentation includes a virtualized rows example. Headless table implementation is standard.
- **Phase 3:** react-resizable-panels, URL search params — both have thorough documentation.
- **Phase 4:** Display density, search highlight, motion animations — simple CSS and well-documented motion API.

No phases require deeper pre-implementation research. The research corpus is complete.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified against npm as of 2026-03-27. React 19 compatibility confirmed for all additions. Package selection grounded in headless/vanilla CSS constraints. |
| Features | HIGH | Patterns verified against Linear redesign docs, enterprise data table UX research, and UX pattern libraries. Feature dependency graph is explicit and grounded in current codebase structure. |
| Architecture | HIGH | Component boundaries and data flow derived from direct codebase audit. Patterns are standard React composition — no novel approaches. AppShell + lifted state + presenter components is the established approach. |
| Pitfalls | HIGH | Research performed against actual codebase (`App.tsx` line numbers referenced). Pitfalls are not theoretical — they are derived from observed code structure (658-line God component, `perPage: 100` hardcoded cap, inline `<style>` blocks). |

**Overall confidence:** HIGH

### Gaps to Address

- **Filter state behavior across vehicle switches:** Research identifies this as a deliberate design decision needed — per-vehicle filter state vs. global filter state. This must be decided before the Toolbar is built in Phase 2. There is no wrong answer, but it must be explicit, not accidental.
- **URL routing scope:** The pitfalls research recommends `?vehicleId=3` URL search params for selected vehicle state. This is a Phase 3 concern but the decision (URL params vs. in-memory state only) should be made in Phase 1 when App-level state is being mapped, to avoid retrofitting.
- **`perPage` limit strategy:** Current hardcoded `perPage: 100` in `App.tsx` line 58 needs an explicit decision: increase to cover max vehicle dataset (621) with virtualization, or add pagination controls. Decide before table implementation begins in Phase 2.

---

## Sources

### Primary (HIGH confidence)
- [@tanstack/react-table npm](https://www.npmjs.com/package/@tanstack/react-table) — v8.21.3 confirmed, React 19 compatible
- [@tanstack/react-virtual npm](https://www.npmjs.com/package/@tanstack/react-virtual) — v3.13.23 confirmed
- [react-resizable-panels npm](https://www.npmjs.com/package/react-resizable-panels) — v4.7.6 confirmed
- [motion npm](https://www.npmjs.com/package/motion) — v12.37.0 confirmed, framer-motion rename documented
- [@floating-ui/react npm](https://www.npmjs.com/package/@floating-ui/react) — v0.27.19 confirmed
- [TanStack Table Virtualization Guide](https://tanstack.com/table/v8/docs/guide/virtualization) — official pairing guidance
- Codebase audit: `frontend/src/App.tsx`, `index.css`, `CommunicationList.tsx`, `.planning/codebase/CONCERNS.md`

### Secondary (MEDIUM confidence)
- [Linear UI redesign (part II)](https://linear.app/now/how-we-redesigned-the-linear-ui) — sidebar, density, visual hierarchy patterns
- [Enterprise Data Table UX — Pencil & Paper](https://www.pencilandpaper.io/articles/ux-pattern-analysis-enterprise-data-tables) — sticky headers, column management, density
- [Filter UX Design Patterns — Pencil & Paper](https://www.pencilandpaper.io/articles/ux-pattern-analysis-enterprise-filtering) — filter chips, active state, clear-all
- [Getting stuck: position:sticky failures — Polypane](https://polypane.app/blog/getting-stuck-all-the-ways-position-sticky-can-fail/) — scroll model pitfall research
- [CSS Grid Fixed Sidebar — Paige Niedringhaus](https://www.paigeniedringhaus.com/blog/use-css-grid-to-make-a-fixed-sidebar-with-scrollable-main-body/) — layout shell implementation
- [Rendering Massive Tables — DEV Community](https://dev.to/lalitkhu/rendering-massive-tables-at-lightning-speed-virtualization-with-virtual-scrolling-2dpp) — virtualization rationale
- [Best Mobile Layout for Data Tables — UX Movement 2026](https://uxmovement.medium.com/the-best-mobile-layout-for-complex-data-tables-e3ced21ce425) — responsive table patterns
- [React State Management 2025 — developerway.com](https://www.developerway.com/posts/react-state-management-2025) — God component split patterns
- [CSS custom properties for React devs — Josh W. Comeau](https://www.joshwcomeau.com/css/css-variables-for-react-devs/) — token system patterns

---
*Research completed: 2026-03-27*
*Ready for roadmap: yes*
