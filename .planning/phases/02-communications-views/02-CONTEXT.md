# Phase 2: Communications Views - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the core data consumption experience: sortable table view with virtualization, improved card view, view toggle with persistence, detail drawer, filter chips with search highlighting, priority grouping, and full responsive behavior. This phase delivers 15 requirements (VIEW-01–08, FILT-01–04, RESP-01–03).

</domain>

<decisions>
## Implementation Decisions

### Table Design
- **D-01:** Table columns: Type badge (colored), Priority dot (high/med/low), Date, Summary (truncated), Comm #, Doc count, Keyword match badges
- **D-02:** Default sort: newest first (communication date descending)
- **D-03:** Row click opens detail drawer (right-side panel)
- **D-04:** TanStack Table + TanStack Virtual for virtualized rendering of 600+ rows
- **D-05:** All columns sortable (date, type, comm number, priority)

### Card View (Improved)
- **D-06:** Carry forward existing CommunicationRow pattern, refine info hierarchy to match table column choices
- **D-07:** Card click also opens detail drawer (consistent with table behavior)

### View Toggle
- **D-08:** Toggle between table and card views — persisted to localStorage
- **D-09:** Toggle lives in the toolbar area (from Phase 1 layout shell)

### Detail Drawer
- **D-10:** Fixed width: 400px on desktop
- **D-11:** Close methods: click outside + X button + Escape key
- **D-12:** Content: everything expanded — full summary, details summary, all products, all documents, keywords. No accordions.
- **D-13:** On mobile: full-screen overlay with back button to close

### Filter UX
- **D-14:** Active filters shown as dismissable chips in a horizontal row below the toolbar — visible only when filters are active
- **D-15:** "Clear all" action removes all active filters at once
- **D-16:** Priority group filters: toggle buttons (High/Med/Low) that select/deselect all types in that priority — refine existing pattern
- **D-17:** Search highlighting: yellow/amber `<mark>` background on matched text in summaries and comm numbers

### Priority Grouping
- **D-18:** Communications can be grouped by priority level with collapsible sections (FILT-04)

### Responsive
- **D-19:** Table → card breakpoint: 768px (tablet landscape and above = table, below = cards)
- **D-20:** Mobile sidebar: hamburger menu opens sidebar as overlay from left
- **D-21:** Mobile detail drawer: full-screen overlay with back button
- **D-22:** Touch targets: minimum 44px on all interactive elements

### Claude's Discretion
- Density toggle (VIEW-08) implementation details — compact/comfortable/spacious row heights
- Card view improvements beyond current pattern
- Exact animation/transition behavior for drawer open/close
- Filter chip visual design details

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Context
- `.planning/PROJECT.md` — Core value, constraints, Linear/Notion aesthetic target
- `.planning/REQUIREMENTS.md` — VIEW-01–08, FILT-01–04, RESP-01–03 are this phase's requirements
- `.planning/ROADMAP.md` — Phase 2 success criteria and dependency on Phase 1

### Phase 1 Decisions
- `.planning/phases/01-foundation/01-CONTEXT.md` — Layout shell grid, CSS modules, AppContext provider, token system — all Phase 2 components build on these

### Research Findings
- `.planning/research/STACK.md` — TanStack Table v8.21.3, TanStack Virtual v3.13.23, react-resizable-panels
- `.planning/research/FEATURES.md` — Table stakes features, detail drawer pattern, mobile card-only
- `.planning/research/ARCHITECTURE.md` — Component boundaries, data flow, build order
- `.planning/research/PITFALLS.md` — Virtualization must be baked in, not bolted on

### Existing Code
- `frontend/src/features/communications/components/CommunicationList.tsx` — Current card-based list to refactor
- `frontend/src/features/communications/hooks/useCommunications.ts` — Query hooks to consume in table/card views
- `frontend/src/client/types.ts` — Communication type, CommType, priority colors/groupings

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `CommunicationRow` component: existing card design with type badge, date, summary, keywords, doc count — refine for improved card view
- `COMM_TYPE_COLORS`, `COMM_PRIORITY_TYPES`, `PRIORITY_COLORS`: color/priority mappings already defined in `types.ts`
- `useCommunicationsQuery` hook: already handles pagination, filtering, type filtering — table/card views consume this
- `useVehicleStatsQuery` hook: provides category breakdown for filter UI

### Established Patterns (from Phase 1)
- CSS modules (`.module.css`) for all new component styles
- AppContext provider for filter state, view preference, selected vehicle
- Feature-area folder structure: new views in `src/features/communications/components/`
- Token system in `index.css` for all design values

### Integration Points
- Toolbar component (from Phase 1) — view toggle, search, filter controls slot in here
- Content area of AppShell — table/card views render here
- Drawer overlays the content area, doesn't push layout
- Filter state from AppContext drives both query params and chip display

</code_context>

<specifics>
## Specific Ideas

- Linear-style table: dense rows, no decorative spacing, every pixel earns its keep
- Detail drawer mirrors Linear's issue detail panel — content-focused, no tabs
- Filter chips should disappear when no filters active (zero-state = clean toolbar)
- Priority dots in table should use the existing `PRIORITY_COLORS` (red/yellow/green)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-communications-views*
*Context gathered: 2026-03-27*
