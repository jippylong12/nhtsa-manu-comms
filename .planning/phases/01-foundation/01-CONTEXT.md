# Phase 1: Foundation - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Establish the structural foundation for the UI/UX overhaul: CSS Grid layout shell with sidebar + main content, extract App.tsx into focused components, migrate inline styles to CSS modules, and audit all tokens. No new features — this phase creates the skeleton that Phases 2–4 build on.

</domain>

<decisions>
## Implementation Decisions

### Sidebar Behavior
- **D-01:** Compact list style — year + model per line, comm count badge, selected highlight (Linear issue-list pattern)
- **D-02:** Click to select — sidebar item highlights, main content loads that vehicle's communications immediately
- **D-03:** Claude's discretion on collapse behavior — decide whether to include a collapse-to-rail toggle on desktop based on screen usage patterns
- **D-04:** "Add Vehicle" button lives at the top of the sidebar, always accessible

### Component Extraction
- **D-05:** Core app state (selectedVehicleId, filters, view prefs) lives in an AppContext provider — any component can consume without prop drilling
- **D-06:** Decompose by feature area: AppShell (layout), Sidebar, Toolbar (filters+search), StatsBar, content views — each owns its own styles
- **D-07:** New layout components in `src/components/layout/`, features stay in existing `src/features/` folders

### CSS Migration
- **D-08:** Migrate inline `<style>` tags to CSS modules (`.module.css`) for scoped class names
- **D-09:** Full token audit — colors, spacing, typography, shadows, transitions — everything references CSS custom properties from `index.css`
- **D-10:** Fix escaped-token patterns: `color-mix()`, hex-alpha (`${typeColor}20`), and hardcoded HSL values must all use the token system

### Layout Shell Grid
- **D-11:** 3-row + sidebar grid: Header (sticky full-width) → Sidebar (independent scroll) + Main (Toolbar sticky + Content scrollable)
- **D-12:** Two independent scroll surfaces — sidebar scrolls independently from main content
- **D-13:** Default sidebar width: 240px

### Claude's Discretion
- Sidebar collapse toggle on desktop (D-03): decide based on whether it adds value for the data scale
- Any token naming additions needed for the full audit
- Exact component file naming and internal structure

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Context
- `.planning/PROJECT.md` — Core value, constraints, out-of-scope boundaries
- `.planning/REQUIREMENTS.md` — LAYOUT-01 through LAYOUT-05 are this phase's requirements
- `.planning/ROADMAP.md` — Phase 1 success criteria (App.tsx < 100 lines, no prop chains > 2)

### Research Findings
- `.planning/research/ARCHITECTURE.md` — Component boundaries, build order, AppShell pattern
- `.planning/research/STACK.md` — react-resizable-panels recommendation for sidebar
- `.planning/research/PITFALLS.md` — Sidebar scroll model and token migration warnings

### Existing Code
- `frontend/src/index.css` — Token system (`:root` custom properties) — the source of truth for design tokens
- `frontend/src/App.tsx` — God component to decompose (658 lines, all state lives here)
- `frontend/src/components/Header.tsx` — First component to migrate (inline styles → CSS module)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `:root` token system in `index.css` — solid foundation with colors, spacing, radii, transitions, typography
- `Header.tsx` — clean, self-contained component, good candidate for first CSS module migration
- `VehicleCard.tsx` — vehicle display patterns that inform sidebar compact list design
- TanStack Query hooks — already manage all server state, AppContext only needs UI state

### Established Patterns
- All components use inline `<style>` tagged template literals — consistent but needs migration
- Feature-based folder structure (`src/features/vehicles/`, `src/features/communications/`) — keep this, add `src/components/layout/`
- `CamelModel` base in backend ensures consistent JSON shape — frontend types mirror this

### Integration Points
- `App.tsx` Dashboard function renders everything — extraction starts here
- `selectedVehicleId` state drives the entire view switch (vehicle grid vs comms view)
- Filter state (`searchTerm`, `selectedTypes`) is tightly coupled to comms query — needs to move to context provider
- `useVehiclesQuery`, `useCommunicationsQuery`, `useVehicleStatsQuery` — these hooks stay as-is, context provider wraps around them

</code_context>

<specifics>
## Specific Ideas

- Linear/Notion aesthetic: clean, dense, minimal chrome — every design choice should push toward information density over decoration
- The 3-row + sidebar grid layout with two independent scroll surfaces is non-negotiable — research flagged single-scroll-surface sidebar as the highest-risk pitfall
- CSS modules chosen specifically for scoped class names to prevent global conflicts during the migration

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-foundation*
*Context gathered: 2026-03-27*
