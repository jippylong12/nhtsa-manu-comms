# Phase 1: Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 1-Foundation
**Areas discussed:** Sidebar behavior, Component extraction, CSS migration, Layout shell grid

---

## Sidebar Behavior

### Vehicle list presentation

| Option | Description | Selected |
|--------|-------------|----------|
| Compact list | Year + model per line, comm count badge, selected highlight — Linear style | ✓ |
| Mini cards | Small card per vehicle with model, year, count, last-fetched | |
| You decide | Claude picks | |

**User's choice:** Compact list (Recommended)

### Selection behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Highlight + load right | Sidebar item highlights, main content loads comms immediately | ✓ |
| Expand inline | Vehicle expands in-sidebar to show summary stats | |
| You decide | Claude picks | |

**User's choice:** Highlight + load right (Recommended)

### Collapse behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, collapse to rail | Thin icon rail (~48px) when collapsed | |
| Always visible | No collapse toggle on desktop | |
| You decide | Claude picks based on screen usage patterns | ✓ |

**User's choice:** You decide

### Add Vehicle placement

| Option | Description | Selected |
|--------|-------------|----------|
| Top of sidebar | Plus button at top of vehicle list | ✓ |
| Both places | Sidebar button + empty-state CTA | |
| You decide | Claude picks placement | |

**User's choice:** Top of sidebar (Recommended)

---

## Component Extraction

### State ownership

| Option | Description | Selected |
|--------|-------------|----------|
| Context provider | AppContext with selectedVehicleId, filters, view prefs | ✓ |
| Top-level component | Keep state in root Dashboard, pass via props (max 2 levels) | |
| You decide | Claude picks | |

**User's choice:** Context provider (Recommended)

### Component boundaries

| Option | Description | Selected |
|--------|-------------|----------|
| By feature area | AppShell, Sidebar, Toolbar, StatsBar, content views | ✓ |
| By responsibility | Layout vs data vs UI primitives | |
| You decide | Claude determines | |

**User's choice:** By feature area (Recommended)

### File organization

| Option | Description | Selected |
|--------|-------------|----------|
| Keep feature folders | New layout components in src/components/layout/ | ✓ |
| Flat components | All shared components in src/components/ | |
| You decide | Claude picks | |

**User's choice:** Keep feature folders

---

## CSS Migration

### Style file approach

| Option | Description | Selected |
|--------|-------------|----------|
| Co-located .css files | Component.css next to Component.tsx | |
| CSS modules | Component.module.css for scoped class names | ✓ |
| You decide | Claude picks | |

**User's choice:** CSS modules

### Token audit scope

| Option | Description | Selected |
|--------|-------------|----------|
| Colors + spacing only | Replace hardcoded values — quick win | |
| Full audit | Colors, spacing, typography, shadows, transitions | ✓ |
| You decide | Claude decides | |

**User's choice:** Full audit

---

## Layout Shell Grid

### Grid structure

| Option | Description | Selected |
|--------|-------------|----------|
| 3-row + sidebar | Header (sticky) + Sidebar (scroll) + Main (Toolbar sticky + Content scroll) | ✓ |
| Simple 2-column | Sidebar + Main (everything in one scroll) | |
| You decide | Claude picks | |

**User's choice:** 3-row + sidebar (Recommended)

### Sidebar width

| Option | Description | Selected |
|--------|-------------|----------|
| 240px | Standard Linear/Notion width | ✓ |
| 280px | More breathing room | |
| You decide | Claude picks | |

**User's choice:** 240px (Recommended)

---

## Claude's Discretion

- Sidebar collapse toggle on desktop — user deferred to Claude's judgment

## Deferred Ideas

None — discussion stayed within phase scope
