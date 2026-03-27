# Phase 2: Communications Views - Research

**Phase:** 2 — Communications Views
**Researched:** 2026-03-27
**Confidence:** HIGH (builds directly on project-level research + Phase 1 foundation)

---

## Research Questions

### 1. How should TanStack Table + Virtual be integrated for 600+ row performance?

**Finding:** TanStack Table v8 is headless — it manages sort/filter/visibility state but renders nothing. TanStack Virtual manages row virtualization. The integration pattern:

1. Create a `useReactTable` instance with column definitions and data
2. Create a `useVirtualizer` instance referencing the scrollable container
3. Render only `virtualizer.getVirtualItems()` rows inside a container whose height equals `virtualizer.getTotalSize()`

**Key implementation details:**
- Row height must be fixed (or estimated) for virtualization to work. Use `--row-height` CSS custom property: compact=32px, comfortable=40px, spacious=52px
- The table container (not the table element) must be the scroll parent with a fixed height
- `overscan: 5` is sufficient for smooth scrolling without excessive DOM nodes
- Sort state managed by TanStack Table's `getSortedRowModel()` — no manual sorting needed
- The `perPage: 100` cap in App.tsx must be raised to fetch all communications for a vehicle. With virtualization, rendering 621 rows costs the same as rendering 20

**Source:** TanStack Table virtualization guide + project PITFALLS.md Pitfall 3

### 2. What is the detail drawer architecture?

**Finding:** The drawer is an overlay panel anchored to the right edge of the main content area. It does NOT push the table/card layout — it overlays with a semi-transparent backdrop.

**Implementation pattern:**
- Position: `fixed` relative to the main content panel (not viewport, to avoid overlapping the sidebar)
- Width: 400px on desktop, full-screen on mobile (<768px)
- Close triggers: X button, Escape key, click on backdrop
- Content: flat layout — no tabs, no accordions. Full summary, detailsSummary, all products, all documents, all keywords
- Transition: slide-in from right (CSS transform, no animation library needed for this)

**State management:**
- `selectedCommId: number | null` stored in CommunicationsView (view-local)
- Row click sets `selectedCommId`
- Drawer reads the full Communication object from the already-fetched list (no additional API call)

### 3. How should filter state and chips work?

**Finding:** Phase 1 moves `searchTerm` and `selectedTypes` to CommunicationsView as local state. Phase 2 adds filter chips as a visual representation of active filters.

**Filter chip architecture:**
- Chips render in a horizontal row between toolbar and content
- Each chip shows the filter value with an X dismiss button
- "Clear all" button appears when 2+ filters are active
- Chip types: search term chip, individual type chips, priority group chips
- Dismissing a chip updates the corresponding state (removes from `selectedTypes` or clears `searchTerm`)

**Filter state behavior decision (from STATE.md blocker):**
- Filters should be **global across vehicle switches** — when user switches vehicles, active type filters persist. Rationale: if a user is looking for TSBs, they're looking for TSBs across vehicles. Clearing filters on vehicle switch forces re-selection.
- Search term clears on vehicle switch (search is vehicle-specific content)

### 4. How should the view toggle and density toggle coexist?

**Finding:** Two independent controls:
- **View toggle**: table vs card — stored in localStorage as `comms-view-mode`
- **Density toggle**: compact/comfortable/spacious — stored in localStorage as `comms-density`
- Density only applies to table view (cards have fixed layout)
- Both live in the toolbar area

**Implementation:**
- `useLocalStorage<'table' | 'card'>('comms-view-mode', 'table')` hook
- `useLocalStorage<'compact' | 'comfortable' | 'spacious'>('comms-density', 'comfortable')` hook
- Density sets `--row-height` CSS custom property on the table container
- Row heights: compact=32px, comfortable=40px, spacious=52px

### 5. How should responsive behavior work for the table?

**Finding:** The table cannot be made usable on phone screens (<768px). The correct pattern is:

- **>=768px (tablet landscape and up):** Show table or card view based on user toggle
- **<768px (phone):** Force card view regardless of toggle setting. Hide the view toggle control.
- The sidebar auto-collapses on mobile (handled by Phase 1 AppShell with react-resizable-panels)
- Touch targets: minimum 44px height on all interactive elements (table rows, filter buttons, drawer close)

**Mobile detail drawer:** Full-screen overlay with a back button in the top bar (not slide-in panel)

### 6. How should priority grouping work (FILT-04)?

**Finding:** Priority grouping is an alternative view mode for the communications list:
- Toggle: a "Group by priority" button in the toolbar
- When active: communications are grouped into collapsible sections (High, Medium, Low)
- Section headers show priority name and count
- Default: High expanded, Medium and Low collapsed
- Works in both table and card views
- Sorting operates within each group

**Implementation:** TanStack Table's `getGroupedRowModel()` with `grouping: ['priority']` column. For card view, manual grouping via array partition.

### 7. How should search highlighting work (FILT-03)?

**Finding:** When `searchTerm` is active, highlight matching text in:
- Communication summary text
- Communication number

**Implementation:** A `HighlightText` utility component:
```tsx
function HighlightText({ text, highlight }: { text: string; highlight: string }) {
  if (!highlight.trim()) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRegex(highlight)})`, 'gi'));
  return <>{parts.map((part, i) =>
    part.toLowerCase() === highlight.toLowerCase()
      ? <mark key={i}>{part}</mark>
      : part
  )}</>;
}
```

Styling: `mark { background: var(--color-highlight, hsl(45, 100%, 50%)); color: var(--text-primary); padding: 0 2px; border-radius: 2px; }`

---

## Component Inventory for Phase 2

| Component | New/Modify | Requirements |
|-----------|-----------|--------------|
| `CommunicationTable.tsx` | NEW | VIEW-01, VIEW-02, VIEW-03 |
| `CommunicationTable.module.css` | NEW | VIEW-01, VIEW-02, VIEW-03 |
| `CommunicationCardGrid.tsx` | NEW (replaces card path) | VIEW-04 |
| `CommunicationCardGrid.module.css` | NEW | VIEW-04 |
| `CommunicationDetail.tsx` | NEW (drawer content) | VIEW-07 |
| `CommunicationDetail.module.css` | NEW | VIEW-07 |
| `DetailDrawer.tsx` | NEW (drawer shell) | VIEW-07 |
| `DetailDrawer.module.css` | NEW | VIEW-07 |
| `ViewToggle.tsx` | NEW | VIEW-05, VIEW-06 |
| `ViewToggle.module.css` | NEW | VIEW-05, VIEW-06 |
| `DensityToggle.tsx` | NEW | VIEW-08 |
| `DensityToggle.module.css` | NEW | VIEW-08 |
| `FilterChips.tsx` | NEW | FILT-01 |
| `FilterChips.module.css` | NEW | FILT-01 |
| `HighlightText.tsx` | NEW (utility) | FILT-03 |
| `useLocalStorage.ts` | NEW (hook) | VIEW-06, VIEW-08 |
| `CommunicationsView.tsx` | MODIFY (integrate new components) | All VIEW, FILT, RESP |
| `CommunicationsView.module.css` | MODIFY | RESP-01, RESP-02, RESP-03 |
| `CommunicationList.tsx` | MODIFY (refine card view) | VIEW-04 |
| `frontend/src/index.css` | MODIFY (add highlight token) | FILT-03 |

---

## Dependency Order

```
Wave 1 (no dependencies):
  ├── Install TanStack Table + Virtual
  ├── useLocalStorage hook
  ├── HighlightText utility
  └── ViewToggle + DensityToggle components

Wave 2 (depends on Wave 1):
  ├── CommunicationTable (uses TanStack Table + Virtual, density, highlight)
  ├── CommunicationCardGrid (uses highlight, existing CommunicationRow)
  └── FilterChips component

Wave 3 (depends on Wave 2):
  ├── DetailDrawer + CommunicationDetail
  ├── CommunicationsView integration (wires everything together)
  └── Responsive behavior + mobile overrides
```

---

## Validation Architecture

### Performance Validation
- Table initial render: < 100ms for 621 rows (measure via React DevTools Profiler)
- Scroll performance: 60fps during continuous scroll (no dropped frames in Chrome Performance tab)
- DOM node count: constant during scroll (verify via Chrome DevTools Elements panel count)
- View toggle: < 50ms switch between table and card

### Functional Validation
- Sort: each column sorts ascending/descending correctly (verify first and last item)
- Filter chips: each chip dismissal removes the correct filter
- Detail drawer: opens with correct communication data, closes via all three methods
- View persistence: refresh page, verify view preference persists
- Density persistence: refresh page, verify density preference persists

### Responsive Validation
- 1440px: full table with all columns, sidebar visible
- 768px: table with reduced columns or card view, sidebar collapsed
- 375px: card-only view, no table toggle, sidebar hamburger menu
- Touch targets: no interactive element smaller than 44x44px

---

## Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| TanStack Virtual row height estimation fails with variable content | MEDIUM | HIGH | Use fixed row heights (density toggle defines exact height). Truncate summary text in table rows. |
| Filter state persistence across vehicle switches confuses users | LOW | MEDIUM | Type filters persist, search clears. Add visual indicator showing persisted filters. |
| Detail drawer z-index conflicts with sidebar resize handle | LOW | LOW | Drawer uses `--z-overlay: 20`, sidebar handle is `--z-base: 0` |
| Mobile card-only fallback loses table sort state | LOW | LOW | Preserve sort state in local state even when table is hidden. When user returns to desktop, sort is maintained. |

---

## RESEARCH COMPLETE

Phase 2 research covers all 15 requirements (VIEW-01-08, FILT-01-04, RESP-01-03). The implementation follows a 3-wave build order with clear dependencies. Key decisions: global type filters across vehicle switches, fixed row heights for virtualization, card-only fallback on mobile.

---

*Phase 2 research for: NHTSA Comms Tracker — Communications Views*
*Researched: 2026-03-27*
