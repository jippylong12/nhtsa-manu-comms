# Pitfalls Research

**Domain:** React dashboard UI/UX overhaul — sidebar navigation, table views, design system migration, dense data presentation
**Researched:** 2026-03-27
**Confidence:** HIGH (project-specific; code audited directly; all findings grounded in observed codebase state)

---

## Critical Pitfalls

### Pitfall 1: Sidebar Breaks the Scroll Contract

**What goes wrong:**
A fixed or sticky sidebar fights the page's existing scroll model. When `overflow: hidden` or `overflow: auto` exists on any ancestor of the sticky element, `position: sticky` stops working against the viewport and checks inset values against that ancestor instead — usually breaking it entirely. The current app's `body` and root `div` likely own the scroll; adding a sidebar layer without reassigning scroll ownership produces a sidebar that either scrolls away with content, duplicates scroll bars, or clips the main area.

**Why it happens:**
The existing app is a single scroll surface. Sidebar layouts require a *two-scroll-surface* model: the sidebar scrolls independently, the content area scrolls independently, and neither affects the other. Developers introduce a sidebar into the existing DOM without restructuring the root layout first.

**How to avoid:**
Adopt a CSS Grid root layout from the start — `grid-template-columns: [sidebar-width] 1fr` with `height: 100vh` and `overflow: hidden` on the grid container. The sidebar gets `overflow-y: auto`, the main content area gets `overflow-y: auto`. Nothing on `body` or `html` gets `overflow: hidden`. Establish this layout shell in Phase 1 (foundation) before touching any feature components.

**Warning signs:**
- Sidebar scrolls away when main content is scrolled
- Adding `overflow: hidden` to a container makes the sidebar "disappear"
- `position: sticky` on sidebar works in isolation but breaks when the full app is assembled
- Mobile viewport shows double scrollbars

**Phase to address:** Layout foundation phase — must be the first structural change. Every subsequent component slots into this grid shell.

---

### Pitfall 2: Migrating Inline Styles Without Establishing a Token Boundary First

**What goes wrong:**
The existing `<style>` blocks inside components already use CSS custom properties from `index.css` (e.g., `var(--space-lg)`, `var(--bg-elevated)`, `var(--border-default)`). If inline styles are migrated by extracting them to external `.css` files component by component — without first auditing which tokens exist, which are missing, and which are duplicated across components — the result is a proliferation of per-component CSS files with locally defined colors and spacing values that diverge from the token system. The design system then has two sources of truth.

**Why it happens:**
The refactor feels mechanical: take the `<style>` block, move it to a `.css` file, done. But `App.tsx` alone has ~200 lines of inline styles and many of the class names (`.comm-row`, `.vehicle-banner`, `.filters-bar`) are local to that component. Moving them out without restructuring creates global class name collisions and scoping ambiguity.

**How to avoid:**
Before extracting any styles: (1) audit every token used across all inline style blocks, (2) identify any hardcoded values that should become tokens (e.g., `${typeColor}20` hex alpha — should be a CSS custom property pattern), (3) define the complete token set in `index.css`, then extract component styles to co-located `.module.css` files or a dedicated `styles/` directory. Use CSS Modules to enforce scoping and eliminate class name collisions across the 6+ components with inline styles.

**Warning signs:**
- Hardcoded hex values appearing in component-level CSS files during migration
- Two components defining a `.badge` class differently
- `color-mix(in srgb, var(--type-color) 30%, transparent)` pattern scattered across multiple components rather than tokenized
- New colors being added to component files instead of `index.css`

**Phase to address:** Design system / token audit phase — complete before any component extraction begins.

---

### Pitfall 3: Table View Renders All 621 Rows Without Virtualization

**What goes wrong:**
The current `perPage: 100` hardcoded limit in `App.tsx` line 58 is an artificial cap, not a feature. When the table view is built, the natural impulse is to remove that cap and render all communications — some vehicles have 621. Rendering 621 table rows as real DOM nodes causes visible jank on initial render, sluggish scrolling (especially on mobile), and memory pressure. The DOM manipulation cost is real at this scale; maintaining 60fps requires only rendering visible rows.

**Why it happens:**
600 items doesn't feel "large" in data terms, but the DOM cost of 600 rows × N cells × expanded detail sections is significant. Without measuring first, developers assume it's fine and only discover the problem in production when users report slow behavior.

**How to avoid:**
Use `@tanstack/react-virtual` (same TanStack family as the existing TanStack Query dependency — minimal new dependency weight) for the table's row virtualization. The table renders only visible rows; total DOM node count stays constant regardless of dataset size. Implement this from the start of the table view, not as a retrofit. Keep the `perPage` limit but surface pagination controls if the dataset exceeds a threshold, or switch to cursor-based loading with the virtualized list.

**Warning signs:**
- Initial table render takes >200ms
- Scrolling in the table stutters or drops frames
- Chrome DevTools shows 600+ `.comm-row` DOM nodes simultaneously
- `perPage: 100` limit removed without a virtualization plan in place

**Phase to address:** Table view implementation phase. Virtualization must be a design constraint going in, not a performance fix added later.

---

### Pitfall 4: God Component Split Creates Prop Drilling Chains

**What goes wrong:**
`App.tsx` is a 658-line God component containing all dashboard state: `selectedVehicleId`, `searchTerm`, `selectedTypes`, `showAddModal`, `showFilterInfo`, `isFetching`, and all handlers. The planned sidebar adds persistent vehicle selection state; the planned table/card toggle adds view preference state; the planned cross-vehicle dashboard adds aggregate state. Splitting `App.tsx` into sub-views without a shared state boundary first results in prop drilling 4-5 levels deep, or an explosion of Context providers that re-render everything on any change.

**Why it happens:**
Splitting a God component feels like progress — and it is — but the split is often done structurally (by moving JSX into separate files) without deciding where state should live. The result is the same component, just spread across multiple files with props threading through every layer.

**How to avoid:**
Before splitting `App.tsx`, map which state is truly global (selected vehicle, current route/view), which is page-scoped (filter state per view), and which is component-local (modal open/close). Use a single lightweight store for global state — either React Context with `useReducer` (no new dependency) or Zustand (minimal, already in the React 19 ecosystem). View-local state stays in the view component. Do not pass `selectedVehicleId` through more than 2 component levels.

**Warning signs:**
- A component receives a prop it doesn't use directly — it just passes it down
- Adding a new filter requires touching 4+ files
- `App.tsx` still imports every hook after the "split"
- `onVehicleSelect` prop appears in `Sidebar`, `Header`, and `Dashboard` simultaneously

**Phase to address:** State architecture phase — define the state boundaries before writing any new components.

---

### Pitfall 5: Responsive Design Added as an Afterthought

**What goes wrong:**
Dense data tables are the hardest UI pattern to make responsive. A table with 5+ columns — date, type, comm number, summary, priority — becomes unreadable on mobile when simply scaled down. The common fallback of adding `overflow-x: auto` to the table container creates a horizontal scroll trap that conflicts with the page's natural vertical scroll. On touch devices, this is particularly disorienting.

**Why it happens:**
Desktop-first development produces a table that looks right on a 1440px screen. Responsive handling is deferred ("we'll add media queries later") and by then the table's column definitions, widths, and cell renderers are baked in.

**How to avoid:**
Design mobile behavior for the table view from the start: on screens below 768px, the table collapses to a card-per-row layout (or shows a subset of priority columns with a "tap to expand" pattern). The card view already exists as a pattern — reuse it as the mobile fallback for the table view. Define the breakpoint behavior in the initial table component spec, not in a follow-up cleanup pass.

**Warning signs:**
- `min-width` values on table columns without a mobile fallback
- `overflow-x: auto` wrapping the whole table on mobile
- The card/table toggle shows "table" on mobile even though table is unusable at that width
- Testing only happens on desktop viewport during development

**Phase to address:** Table view implementation phase, in parallel with desktop implementation — not after.

---

### Pitfall 6: Design Documentation Diverges From Code Immediately

**What goes wrong:**
A style guide, component library doc, and wireframe set are created as deliverables during the overhaul. Three weeks later, a button's border radius gets tweaked, a new spacing token is added, and the priority color for "medium" shifts slightly. None of it gets reflected in the docs because updating documentation is a separate manual step that gets deprioritized under deadline pressure. The docs become aspirational fiction.

**Why it happens:**
Documentation and code live in separate systems with no mechanical link. The only enforcement is discipline — which is not a reliable system.

**How to avoid:**
For the style guide specifically: document tokens from `index.css` in a living format — a React component (`<TokenShowcase />`) that renders actual token values from CSS custom properties at runtime. The component IS the source of truth because it reads the real values. For component documentation, add JSDoc prop comments to every shared component; use tooling like `react-docgen` or Storybook to auto-generate docs from source. Wireframes are one-time artifacts (acceptable to let drift) but the style guide and component API docs must be code-backed.

**Warning signs:**
- Style guide is a Figma file or a static Markdown document with hardcoded hex values
- A color in the docs doesn't match the rendered app
- Component prop tables in docs are written by hand, not generated
- Sprint ends with "update docs" still in the backlog

**Phase to address:** Design documentation phase — establish the code-backed documentation pattern as the first doc deliverable, before writing any standalone Markdown style guides.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Leaving `perPage: 100` cap during table view build | Avoids virtualization complexity | Users with 600-comm vehicles hit an invisible ceiling with no UI explanation | Never — surface the limit or remove it with virtualization |
| CSS Modules for new components, `<style>` blocks left in old ones | Unblocks new work fast | Mixed styling conventions across codebase; future engineers can't predict where styles live | Temporarily acceptable during migration, but set a deadline to complete the extraction |
| `localStorage` for view toggle persistence without abstraction | Simple one-liner | Each new persistent preference adds another raw `localStorage` key; no type safety, no migration path | Acceptable for MVP; extract to a `usePreferences` hook before adding a third persistent state |
| Hardcoding `width: 240px` for sidebar instead of a CSS custom property | Fast to ship | Sidebar width can't be changed from the design token system; responsive overrides need to override in multiple places | Never — `--sidebar-width` token costs nothing |
| Adding sidebar as a fixed overlay instead of restructuring root layout | Zero layout refactor | Mobile behavior requires separate treatment; sidebar and content scrolling fight each other | Never for desktop primary layout |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Rendering all communications as DOM nodes | >200ms initial render, frame drops during scroll | `@tanstack/react-virtual` for table rows from the start | 200+ rows on mid-range hardware |
| TanStack Query refetch triggered by sidebar vehicle selection causing full re-render | List flicker on every vehicle switch | Ensure `staleTime` is set (already 5 min) and query keys are stable; use `keepPreviousData` on vehicle switch | Every navigation event |
| `useMemo` on filters object (`App.tsx` line 56) not memoizing deep-equal objects | Filter change triggers unnecessary API call when values are identical | Stable filter object reference via `useMemo` with correct deps — already done, but verify deps array stays complete as new filter fields are added | When `selectedTypes` array reference changes without value change |
| CSS transitions on every `.comm-row` hover for 600+ rendered rows | Sluggish hover response, GPU layer bloat | Limit transitions to opacity and transform (compositor-only); avoid transitioning `background-color` on rows in a virtualized list | 600+ rows with CSS `transition: background` |
| Sidebar vehicle list re-rendering on every communications update | Sidebar flickers when data refreshes | Memoize `VehicleList` items; ensure TanStack Query vehicles query is separate from communications query (already is) | Each TanStack Query refetch cycle |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Table view replaces card view as the only option (no toggle) | Power users prefer table scanning, but new users orient with cards | Persistent toggle with `localStorage` memory; default to table on desktop, card on mobile |
| View toggle resets scroll position | User scrolls to item 80, toggles view, loses position | Preserve approximate scroll position (by visible item index) across view toggle |
| Sidebar selection state not reflected in URL | User can't share a link to a specific vehicle's communications; back button doesn't navigate between vehicles | Use URL search params (`?vehicleId=3`) for selected vehicle state — pure frontend routing, no backend change |
| Sidebar shows all vehicles but gives no visual cue for which have new/unread comms | User has to click every vehicle to check for updates | Surface "last fetched" age per vehicle in the sidebar (data already exists in vehicle stats) |
| Dense table prioritizes comm number over summary | Comm numbers are meaningless to users; summary is the signal | Lead with summary, show comm number as secondary metadata |
| Filter chips disappear when switching vehicles | User applies TSB+PIT filter, switches vehicle, filters reset | Per-vehicle filter state vs. global filter state is a deliberate design decision — make it explicit, not accidental |
| Cross-vehicle dashboard shows aggregate counts with no way to drill down | Aggregate "621 communications" is noise without a path to specifics | Every stat on the dashboard should be a clickable filter that pre-populates the vehicle view |

---

## "Looks Done But Isn't" Checklist

- [ ] **Sidebar:** Works on desktop — verify it collapses gracefully on tablet (768px) and mobile (375px) with a hamburger/drawer pattern
- [ ] **Table view:** Renders correctly — verify sort state persists across vehicle switches and does not conflict with the API's sort order
- [ ] **View toggle:** Shows correct icon/label — verify the selected view is remembered after page refresh (`localStorage`)
- [ ] **Design tokens:** All tokens defined in `index.css` — verify no hardcoded hex values exist in component-level CSS files after migration (grep for `#[0-9a-fA-F]{3,6}` in `src/`)
- [ ] **Responsive layout:** Looks fine on Chrome desktop — verify on actual mobile viewport (375px) and that the table collapses to card layout, not a horizontal scroll trap
- [ ] **Communications list with sidebar:** Renders vehicle comms — verify that navigating between vehicles clears the previous vehicle's filter state as intended (or preserves it — pick one, document it)
- [ ] **Component documentation:** Written — verify that token showcase reads live CSS values, not hardcoded strings
- [ ] **Sidebar scroll:** Vehicle list scrolls correctly — verify that a list of 20+ vehicles scrolls independently without affecting the main content scroll

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Sidebar breaks scroll model | HIGH | Requires root layout restructure — CSS Grid wrapper around `#root`; all scroll assignments change; touches every component's positioning context |
| Token migration produces two sources of truth | MEDIUM | Audit grep for hardcoded values; extract to tokens; standardize on CSS Modules; 2-3 day cleanup pass |
| Table renders all 600 rows without virtualization | MEDIUM | Retrofit `@tanstack/react-virtual`; table row component must accept a `virtualRow` prop; layout changes needed for fixed-height rows |
| God component split creates prop drilling | HIGH | Requires state architecture redesign mid-sprint; introduces risk of regressions in existing filter/fetch behavior |
| Design docs diverge from code | LOW | Delete static docs; build token showcase component; add JSDoc to components; one-time 1-day effort |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Sidebar breaks scroll model | Phase 1: Layout foundation | CSS Grid root shell renders; sidebar and content scroll independently; no `overflow: hidden` on ancestors |
| Inline style migration creates two token sources | Phase 1: Design system audit | `grep -r '#[0-9a-fA-F]' frontend/src/` returns zero results (outside `index.css`) |
| Table renders 600+ rows without virtualization | Phase 2: Table view | Chrome DevTools shows constant DOM node count while scrolling; initial render <100ms |
| God component split creates prop drilling | Phase 1: State architecture | `selectedVehicleId` is not passed as a prop more than 2 levels deep anywhere in the tree |
| Responsive design added as afterthought | Phase 2: Table view | Table shows card layout below 768px; no horizontal scroll trap on 375px viewport |
| Design docs diverge immediately | Phase 3: Documentation | Token showcase component reads live CSS values; no static hex in docs |
| Sidebar state not in URL | Phase 2: Navigation wiring | `?vehicleId=3` URL loads correct vehicle; back button works; shareable link works |

---

## Sources

- Codebase audit: `/Users/marcus.salinas/PycharmProjects/nhtsa-manu-comms/frontend/src/App.tsx`, `index.css`, `CommunicationList.tsx`, `.planning/codebase/CONCERNS.md`
- [Getting stuck: all the ways position:sticky can fail — Polypane](https://polypane.app/blog/getting-stuck-all-the-ways-position-sticky-can-fail/)
- [Use CSS Grid for Fixed Sidebar with Scrollable Main — Paige Niedringhaus](https://www.paigeniedringhaus.com/blog/use-css-grid-to-make-a-fixed-sidebar-with-scrollable-main-body/)
- [Rendering Massive Tables: Virtualization with Virtual Scrolling — DEV Community](https://dev.to/lalitkhu/rendering-massive-tables-at-lightning-speed-virtualization-with-virtual-scrolling-2dpp)
- [The Best Mobile Layout for Complex Data Tables — UX Movement, 2026](https://uxmovement.medium.com/the-best-mobile-layout-for-complex-data-tables-e3ced21ce425)
- [Optimizing Large Lists: Virtualization vs. Pagination — IGNEK Blog](https://www.ignek.com/blog/optimizing-large-lists-in-react-virtualization-vs-pagination)
- [Documenting Components — Eight Shapes / Medium](https://medium.com/eightshapes-llc/documenting-components-9fe59b80c015)
- [CSS custom properties for React devs — Josh W. Comeau](https://www.joshwcomeau.com/css/css-variables-for-react-devs/)
- [React State Management in 2025 — developerway.com](https://www.developerway.com/posts/react-state-management-2025)

---
*Pitfalls research for: NHTSA Comms Tracker UI/UX overhaul*
*Researched: 2026-03-27*
