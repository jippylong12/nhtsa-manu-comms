# Phase 4: Design Documentation - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Create living design documentation that captures decisions from Phases 1–3: component library with props/variants/examples, visual style guide backed by CSS custom properties, UX patterns guide, and wireframe specs for each screen. Four requirements: DOCS-01–04.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion (Full)
User deferred all decisions to Claude. The following guidelines apply:

- **D-01:** All documentation decisions are at Claude's discretion — format, location, scope, structure
- **D-02:** Documentation should be code-backed where possible (reading live CSS custom properties, referencing real component files)
- **D-03:** Must be maintainable — prefer formats that don't drift from the code

### Suggested Approach (Claude's Plan)
- Component library: Markdown docs in `docs/components/` with props tables, usage examples, and variant screenshots
- Style guide: Token showcase page (can be a route in the app or standalone HTML) that reads actual `:root` custom properties
- UX patterns: Markdown guide in `docs/patterns/` covering navigation, data views, filtering, responsive behavior
- Wireframes: ASCII/markdown layout specs in `docs/wireframes/` for each screen (overview, comms table, comms card, detail drawer, mobile)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Context
- `.planning/PROJECT.md` — Design documentation requirements
- `.planning/REQUIREMENTS.md` — DOCS-01 through DOCS-04
- `.planning/ROADMAP.md` — Phase 4 success criteria

### Prior Phase Decisions (everything Phase 4 documents)
- `.planning/phases/01-foundation/01-CONTEXT.md` — Layout shell, tokens, CSS modules, component structure
- `.planning/phases/02-communications-views/02-CONTEXT.md` — Table, card, drawer, filters, responsive
- `.planning/phases/03-dashboard-navigation/03-CONTEXT.md` — Overview, sidebar resize, drill-in

### Existing Code (to document)
- `frontend/src/index.css` — Token system source of truth
- `frontend/src/components/layout/` — Layout components (from Phase 1)
- `frontend/src/features/` — Feature components (from Phases 1-3)

</canonical_refs>

<code_context>
## Existing Code Insights

### What Gets Documented
- All components built in Phases 1–3: AppShell, Sidebar, Toolbar, StatsBar, CommunicationTable, CommunicationCard, DetailDrawer, OverviewDashboard, VehicleStatCard, FilterChips
- Token system: colors, spacing, typography, shadows, transitions, radii from `index.css`
- Patterns: sidebar navigation, view toggle, filter chips, detail drawer, responsive breakpoints

### Integration Points
- Docs can reference component files directly with relative paths
- Style guide can import/read CSS custom properties programmatically
- Wireframes capture the final state of each screen after Phases 1–3

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Claude has full discretion on all design documentation decisions.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-design-documentation*
*Context gathered: 2026-03-27*
