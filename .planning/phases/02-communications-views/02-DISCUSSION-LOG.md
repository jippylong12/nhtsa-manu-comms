# Phase 2: Communications Views - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 2-Communications Views
**Areas discussed:** Table design, Detail drawer, Filter UX overhaul, Responsive breakpoints

---

## Table Design

### Columns

| Option | Description | Selected |
|--------|-------------|----------|
| Type badge + Date + Summary + Comm # | Core 4 columns | ✓ |
| Priority indicator | High/med/low dot | ✓ |
| Document count | Number of associated documents | ✓ |
| Keyword matches | Inline keyword badges | ✓ |

**User's choice:** All columns selected

### Default sort

| Option | Description | Selected |
|--------|-------------|----------|
| Newest first | Communication date descending | ✓ |
| Priority then date | High priority first, then by date | |
| You decide | Claude picks | |

**User's choice:** Newest first (Recommended)

### Row click behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Open detail drawer | Right-side panel slides open | ✓ |
| Expand inline | Row expands below | |
| You decide | Claude picks | |

**User's choice:** Open detail drawer (Recommended)

---

## Detail Drawer

### Width

| Option | Description | Selected |
|--------|-------------|----------|
| 400px | Fixed width, enough for content | ✓ |
| 50% of main area | Responsive scaling | |
| You decide | Claude picks | |

**User's choice:** 400px (Recommended)

### Close behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Click outside + X + Esc | All three methods | ✓ |
| X button only | Explicit close only | |
| You decide | Claude picks | |

**User's choice:** Click outside + X + Esc (Recommended)

### Content layout

| Option | Description | Selected |
|--------|-------------|----------|
| Everything expanded | Full detail, no accordions | ✓ |
| Sectioned with accordions | Collapsed by default | |
| You decide | Claude picks | |

**User's choice:** Everything expanded (Recommended)

---

## Filter UX Overhaul

### Chip placement

| Option | Description | Selected |
|--------|-------------|----------|
| Below toolbar | Horizontal row, visible only when filters active | ✓ |
| Inside toolbar | Inline with search/filter buttons | |
| You decide | Claude picks | |

**User's choice:** Below toolbar (Recommended)

### Priority group UX

| Option | Description | Selected |
|--------|-------------|----------|
| Toggle buttons | High/Med/Low toggles | ✓ |
| Dropdown groups | Dropdown with checkboxes | |
| You decide | Claude picks | |

**User's choice:** Toggle buttons (Recommended)

### Search highlighting

| Option | Description | Selected |
|--------|-------------|----------|
| Yellow background mark | Standard `<mark>` style | ✓ |
| Bold + color | Bold weight + primary color | |
| You decide | Claude picks | |

**User's choice:** Yellow background mark (Recommended)

---

## Responsive Breakpoints

### Table → cards breakpoint

| Option | Description | Selected |
|--------|-------------|----------|
| 768px | Standard tablet breakpoint | ✓ |
| 1024px | More conservative, cards on all tablets | |
| You decide | Claude picks | |

**User's choice:** 768px (Recommended)

### Mobile navigation

| Option | Description | Selected |
|--------|-------------|----------|
| Bottom sheet sidebar | Hamburger menu, overlay from left | ✓ |
| Top dropdown | Vehicle selector in header | |
| You decide | Claude picks | |

**User's choice:** Bottom sheet sidebar (Recommended)

### Mobile drawer

| Option | Description | Selected |
|--------|-------------|----------|
| Full-screen overlay | Back button to close | ✓ |
| Bottom sheet | Swipeable sheet from bottom | |
| You decide | Claude picks | |

**User's choice:** Full-screen overlay (Recommended)

---

## Claude's Discretion

- Density toggle implementation details
- Card view improvements beyond current pattern
- Drawer animation/transition details
- Filter chip visual design

## Deferred Ideas

None — discussion stayed within phase scope
