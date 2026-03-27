# NHTSA Comms Tracker — UI/UX Beautification

## What This Is

A holistic UI/UX overhaul of the NHTSA Manufacturer Communications Tracker. The app already works — it fetches, stores, filters, and displays vehicle communications from the NHTSA API. This milestone is about making it *feel* professional: rethinking layout, navigation, data presentation, and interaction patterns. The target aesthetic is Linear/Notion — clean, dense, keyboard-friendly, minimal chrome.

## Core Value

Users can efficiently scan, filter, and consume 600+ manufacturer communications without the UI getting in the way.

## Requirements

### Validated

- ✓ Vehicle CRUD (add, delete, list) — existing
- ✓ NHTSA communication fetching with SSE progress — existing
- ✓ Communication listing with type/date/keyword filtering — existing
- ✓ Communication type detection (TSB, PIT, PIC, PIP, etc.) — existing
- ✓ Priority-based color coding (high/medium/low) — existing
- ✓ Search across summaries and comm numbers — existing
- ✓ Vehicle stats (total count, last 30 days, category breakdown) — existing
- ✓ Expandable communication detail with documents and products — existing
- ✓ Dark theme with CSS custom properties — existing

### Active

- [ ] Sidebar navigation with persistent vehicle list
- [ ] Table view for communications (sortable columns, dense rows)
- [ ] Card view for communications (improved from current)
- [ ] View toggle (table ↔ card) with persistence
- [ ] Cross-vehicle dashboard/overview on landing
- [ ] Improved information hierarchy — surface what matters at a glance
- [ ] Fully responsive design (phone, tablet, desktop)
- [ ] Component library with documented reusable components
- [ ] UX patterns guide (navigation, data organization, interactions)
- [ ] Visual style guide (typography, spacing, colors, iconography)
- [ ] Wireframes/layout specs for each screen

### Out of Scope

- Backend API changes — this is a frontend-only milestone
- New data features (new filters, new fields, new endpoints)
- Authentication / multi-user support
- Theming overhaul — current dark theme is fine, just polish it
- Keyboard shortcuts (future milestone)

## Context

**Current state:** Single-page React app with two views — vehicle cards grid and communications detail. Navigation is click-to-drill (vehicles → comms). All components are in a flat feature-based structure with inline `<style>` tags. No component library, no design documentation.

**Stack:** React 19 + Vite + TypeScript, TanStack Query, Lucide icons, date-fns. All CSS is inline via tagged template literals in components — no external CSS framework.

**Data scale:** Vehicles have 88–621 communications each. Communications have 14 type categories across 3 priority levels. Each comm has associated products, documents, and keyword matches.

**Design reference:** Linear / Notion — clean, information-dense, keyboard-driven, minimal decorative chrome. Professional data tool feel.

## Constraints

- **Stack**: Keep React + Vite + TypeScript. No CSS framework (Tailwind, etc.) — continue with CSS custom properties approach.
- **Backend**: No backend changes in this milestone. Frontend-only.
- **Theme**: Keep existing dark theme. Refine, don't replace.
- **Compatibility**: Must work well across desktop, tablet, and mobile.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Sidebar navigation | User wants persistent vehicle list visible while browsing comms | — Pending |
| Table + Card toggle | 600+ comms need both dense scanning (table) and rich detail (card) | — Pending |
| Linear/Notion aesthetic | Clean, dense, professional — matches data-heavy tool needs | — Pending |
| Design documentation | Component library + UX patterns + style guide + wireframes for iteration | — Pending |
| No CSS framework | Continue CSS custom properties approach — already established, works well | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-27 after initialization*
