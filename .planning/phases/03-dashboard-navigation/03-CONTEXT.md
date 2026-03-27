# Phase 3: Dashboard + Navigation - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the cross-vehicle overview landing page and upgrade the sidebar with drag-resize and collapse interactions. Two requirements: DASH-01 (overview with aggregated stats) and DASH-02 (counts, activity, priority breakdown per vehicle).

</domain>

<decisions>
## Implementation Decisions

### Overview Layout
- **D-01:** Vehicle stat cards — one card per tracked vehicle showing comm count, priority breakdown (high/med/low), and last fetched timestamp
- **D-02:** Click any vehicle card to drill into that vehicle's communications view
- **D-03:** Overview is the default view when no vehicle is selected (replaces current hero section + vehicle grid)
- **D-04:** Empty state: centered CTA with "Add your first vehicle" and prominent button — renders in main content area

### Sidebar Resize/Collapse
- **D-05:** Both drag-resize AND collapse — react-resizable-panels handles the full interaction
- **D-06:** Collapse to a thin rail with toggle button to expand
- **D-07:** Resize/collapse state persists across sessions (localStorage via react-resizable-panels)

### Vehicle Drill-in
- **D-08:** Same behavior as sidebar click — sets selectedVehicleId in AppContext, content area swaps from overview to comms view, sidebar highlights the selected vehicle
- **D-09:** No animation on transition — instant content swap (Linear pattern)

### Claude's Discretion
- Vehicle card design details (layout, which stats to emphasize, priority visualization)
- Sidebar rail width and icon choices when collapsed
- Overview card grid responsive behavior (how many columns at each breakpoint)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Context
- `.planning/PROJECT.md` — Core value, Linear/Notion aesthetic
- `.planning/REQUIREMENTS.md` — DASH-01, DASH-02
- `.planning/ROADMAP.md` — Phase 3 success criteria

### Prior Phase Decisions
- `.planning/phases/01-foundation/01-CONTEXT.md` — Layout shell, sidebar 240px, AppContext, CSS modules
- `.planning/phases/02-communications-views/02-CONTEXT.md` — Responsive breakpoints (768px), mobile sidebar overlay, priority colors

### Research Findings
- `.planning/research/STACK.md` — react-resizable-panels v4.7.6 for sidebar resize/collapse
- `.planning/research/FEATURES.md` — Cross-vehicle dashboard as differentiator
- `.planning/research/ARCHITECTURE.md` — OverviewDashboard as independent component

### Existing Code
- `frontend/src/client/types.ts` — VehicleStats interface, CategoryStats, priority groupings
- `frontend/src/features/vehicles/hooks/useVehicles.ts` — useVehiclesQuery for vehicle list
- `frontend/src/features/communications/hooks/useCommunications.ts` — useVehicleStatsQuery for per-vehicle stats

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `useVehiclesQuery` — fetches all tracked vehicles, provides the data for overview cards
- `useVehicleStatsQuery` — fetches comm stats per vehicle (total, last 30 days, categories)
- `VehicleCard` component — existing card pattern to adapt for overview stat cards
- `COMM_PRIORITY_TYPES`, `PRIORITY_COLORS` — priority groupings and colors from types.ts

### Established Patterns (from Phase 1 & 2)
- AppContext: `selectedVehicleId = null` means show overview, non-null means show comms
- CSS modules for all new components
- Layout shell: overview renders in the main content area slot

### Integration Points
- AppShell content area: renders OverviewDashboard when no vehicle selected, CommunicationsView when selected
- Sidebar: react-resizable-panels wraps the sidebar panel for resize/collapse
- Vehicle cards in overview: onClick sets selectedVehicleId in AppContext (same as sidebar click)

</code_context>

<specifics>
## Specific Ideas

- Overview stat cards should surface priority breakdown visually (small bar or dots showing high/med/low distribution)
- The overview replaces the current hero section entirely — no marketing copy, just data
- Sidebar collapse to rail is especially useful on smaller desktop screens (1024-1280px range)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-dashboard-navigation*
*Context gathered: 2026-03-27*
