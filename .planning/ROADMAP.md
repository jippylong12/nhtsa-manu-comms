# Roadmap: NHTSA Comms Tracker — UI/UX Beautification

## Overview

This milestone transforms a functional single-page CRUD interface into a professional-grade data tool matching Linear/Notion quality. Phase 1 establishes the structural prerequisite — CSS Grid layout shell, design token system, and God component decomposition — that every subsequent feature depends on. Phase 2 builds the centerpiece communications views (table + card) with the filter and responsive patterns. Phase 3 wires the cross-vehicle overview dashboard and interactive sidebar. Phase 4 closes with design documentation. The result: users can efficiently scan, filter, and consume 600+ manufacturer communications without the UI getting in the way.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation** - CSS Grid layout shell, design tokens, primitive components, App.tsx decomposition
- [ ] **Phase 2: Communications Views** - Table + card views, filters, responsive layout, detail drawer
- [ ] **Phase 3: Dashboard + Navigation** - Cross-vehicle overview, resizable sidebar, interactive navigation
- [ ] **Phase 4: Design Documentation** - Component library, style guide, UX patterns, wireframes

## Phase Details

### Phase 1: Foundation
**Goal**: The app has a professional layout shell, unified design token system, and decomposed component architecture that all subsequent features can slot into
**Depends on**: Nothing (first phase)
**Requirements**: LAYOUT-01, LAYOUT-02, LAYOUT-03, LAYOUT-04, LAYOUT-05
**Success Criteria** (what must be TRUE):
  1. User sees a two-column layout with a persistent sidebar showing the vehicle list alongside all main content areas
  2. Sidebar remains visible and vehicle list is accessible while scrolling any amount of content in the main area
  3. Header and filter bar stay fixed at the top of the viewport when the user scrolls through 600+ communications
  4. All CSS colors and spacing values reference tokens from `index.css` — no hardcoded hex values exist in component files
  5. App.tsx is under 100 lines and state ownership is explicit — no prop chains deeper than 2 levels
**Plans**: TBD
**UI hint**: yes

### Phase 2: Communications Views
**Goal**: Users can scan, sort, and drill into 600+ communications via a dense table view and improved card view, with active filter controls and responsive behavior on all screen sizes
**Depends on**: Phase 1
**Requirements**: VIEW-01, VIEW-02, VIEW-03, VIEW-04, VIEW-05, VIEW-06, VIEW-07, VIEW-08, FILT-01, FILT-02, FILT-03, FILT-04, RESP-01, RESP-02, RESP-03
**Success Criteria** (what must be TRUE):
  1. User can sort the communications table by date, type, comm number, and priority with no scroll jank on 600+ rows
  2. User can toggle between table and card views and the preference is remembered after a browser refresh
  3. User can open a communication detail in a right-side drawer without losing their place in the list
  4. Active filters display as dismissable chips and a "clear all" action removes all filters at once
  5. On a phone screen, the table collapses to card-only layout and the sidebar hides with a toggle to show it
**Plans**: TBD
**UI hint**: yes

### Phase 3: Dashboard + Navigation
**Goal**: Users land on a cross-vehicle overview that surfaces aggregate stats, and the sidebar supports resize and collapse interactions
**Depends on**: Phase 2
**Requirements**: DASH-01, DASH-02
**Success Criteria** (what must be TRUE):
  1. When no vehicle is selected, user sees a cross-vehicle dashboard with communication counts, recent activity, and priority breakdown per vehicle
  2. User can click any vehicle stat on the overview to drill directly into that vehicle's communications view
  3. User can drag the sidebar divider to resize it and collapse it to a thin rail, with the preference persisting across sessions
**Plans**: TBD
**UI hint**: yes

### Phase 4: Design Documentation
**Goal**: The component library, visual style guide, UX patterns guide, and screen wireframes exist as living references that capture the decisions made in Phases 1–3
**Depends on**: Phase 3
**Requirements**: DOCS-01, DOCS-02, DOCS-03, DOCS-04
**Success Criteria** (what must be TRUE):
  1. Every reusable component has documented props, variants, and a live example that renders correctly
  2. All design tokens (colors, typography, spacing, radii) are showcased in a browsable style guide backed by the actual CSS custom properties
  3. Navigation, data organization, and interaction conventions are described in a UX patterns guide that a new developer could follow to build a consistent new feature
  4. A wireframe or layout spec exists for each screen (vehicle list, comms table, comms card, detail drawer, overview dashboard)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 0/TBD | Not started | - |
| 2. Communications Views | 0/TBD | Not started | - |
| 3. Dashboard + Navigation | 0/TBD | Not started | - |
| 4. Design Documentation | 0/TBD | Not started | - |
