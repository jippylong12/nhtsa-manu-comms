# Requirements: NHTSA Comms Tracker — UI/UX Beautification

**Defined:** 2026-03-27
**Core Value:** Users can efficiently scan, filter, and consume 600+ manufacturer communications without the UI getting in the way.

## v1 Requirements

### Layout

- [x] **LAYOUT-01**: App uses a CSS Grid layout shell with sidebar column and main content area
- [x] **LAYOUT-02**: Sidebar displays persistent vehicle list visible while browsing communications
- [x] **LAYOUT-03**: Sidebar is resizable and collapsible via react-resizable-panels
- [x] **LAYOUT-04**: Header and filter bar remain sticky while scrolling content
- [x] **LAYOUT-05**: God component (App.tsx) is decomposed into focused components with clear state ownership

### Data Views

- [ ] **VIEW-01**: User can view communications in a dense sortable table (TanStack Table + Virtual)
- [ ] **VIEW-02**: Table columns are sortable by date, type, comm number, and priority
- [ ] **VIEW-03**: Table supports row virtualization for 600+ communications without scroll jank
- [ ] **VIEW-04**: User can view communications in an improved card layout with better info hierarchy
- [ ] **VIEW-05**: User can toggle between table and card views
- [ ] **VIEW-06**: View preference persists in localStorage across sessions
- [ ] **VIEW-07**: User can open communication detail in a right-side drawer panel (Linear-style)
- [ ] **VIEW-08**: User can adjust row density (compact / comfortable / spacious)

### Filters

- [ ] **FILT-01**: Active filters display as dismissable chips with a "clear all" action
- [ ] **FILT-02**: Priority group filters have improved visual hierarchy and toggle UX
- [ ] **FILT-03**: Search matches are highlighted in communication summaries and comm numbers
- [ ] **FILT-04**: Communications can be grouped by priority level with collapsible sections

### Dashboard

- [ ] **DASH-01**: Landing page shows cross-vehicle overview with aggregated stats
- [ ] **DASH-02**: Overview displays communication counts, recent activity, and priority breakdown per vehicle

### Responsive

- [ ] **RESP-01**: Table view collapses to card-only on mobile screens
- [ ] **RESP-02**: Sidebar auto-collapses on mobile with toggle to show/hide
- [ ] **RESP-03**: All interactive elements have touch-friendly tap targets (min 44px)

### Design Documentation

- [ ] **DOCS-01**: Component library with documented reusable components, props, variants, and live examples
- [ ] **DOCS-02**: Visual style guide with code-backed token showcase (colors, typography, spacing, radii)
- [ ] **DOCS-03**: UX patterns guide covering navigation, data organization, and interaction conventions
- [ ] **DOCS-04**: Wireframe/layout specs for each screen (vehicle list, comms table, comms card, detail drawer, overview)

## v2 Requirements

### Advanced Interactions

- **ADV-01**: Keyboard shortcuts for navigation, filtering, and view switching
- **ADV-02**: Bulk selection and actions on communications
- **ADV-03**: Saved filter presets per vehicle
- **ADV-04**: URL-based deep linking to specific vehicles and communications

### Animation

- **ANIM-01**: Sidebar collapse/expand animation via motion library
- **ANIM-02**: View toggle crossfade transition
- **ANIM-03**: Row expand animation in card view
- **ANIM-04**: Tooltip/popover animations via @floating-ui/react

## Out of Scope

| Feature | Reason |
|---------|--------|
| Backend API changes | Frontend-only milestone |
| New data features | No new filters, fields, or endpoints |
| Authentication | No multi-user support needed |
| Theme overhaul | Dark theme is fine, just refine it |
| CSS framework (Tailwind, etc.) | Continue CSS custom properties approach |
| React Router | selectedVehicleId state is sufficient; no deep-linking requirement in v1 |
| Real-time/WebSocket updates | SSE streaming for fetches is sufficient |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| LAYOUT-01 | Phase 1 | Complete |
| LAYOUT-02 | Phase 1 | Complete |
| LAYOUT-03 | Phase 1 | Complete |
| LAYOUT-04 | Phase 1 | Complete |
| LAYOUT-05 | Phase 1 | Complete |
| VIEW-01 | Phase 2 | Pending |
| VIEW-02 | Phase 2 | Pending |
| VIEW-03 | Phase 2 | Pending |
| VIEW-04 | Phase 2 | Pending |
| VIEW-05 | Phase 2 | Pending |
| VIEW-06 | Phase 2 | Pending |
| VIEW-07 | Phase 2 | Pending |
| VIEW-08 | Phase 2 | Pending |
| FILT-01 | Phase 2 | Pending |
| FILT-02 | Phase 2 | Pending |
| FILT-03 | Phase 2 | Pending |
| FILT-04 | Phase 2 | Pending |
| RESP-01 | Phase 2 | Pending |
| RESP-02 | Phase 2 | Pending |
| RESP-03 | Phase 2 | Pending |
| DASH-01 | Phase 3 | Pending |
| DASH-02 | Phase 3 | Pending |
| DOCS-01 | Phase 4 | Pending |
| DOCS-02 | Phase 4 | Pending |
| DOCS-03 | Phase 4 | Pending |
| DOCS-04 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 24 total
- Mapped to phases: 24
- Unmapped: 0

---
*Requirements defined: 2026-03-27*
*Last updated: 2026-03-27 after roadmap creation — full traceability established*
