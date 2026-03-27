# Phase 1: Foundation — Research

**Researched:** 2026-03-27
**Phase scope:** CSS Grid layout shell, design tokens, primitive components, App.tsx decomposition
**Sources:** `.planning/research/ARCHITECTURE.md`, `STACK.md`, `PITFALLS.md`, `FEATURES.md`

## Key Findings for Phase 1

### 1. CSS Grid Root Layout is the Critical Path

The sidebar + main content model requires restructuring the app's scroll surfaces. Current state: single scroll surface on `body`. Target: two independent scroll surfaces (sidebar and main content).

**Implementation:**
- CSS Grid on a root `AppShell` component: `grid-template-columns: var(--sidebar-width) 1fr; height: 100vh`
- Sidebar: `overflow-y: auto` (independent scroll)
- Main content: `overflow-y: auto` (independent scroll)
- Grid container: `overflow: hidden` (prevents body scroll leak)
- Header row spans full width, sticky at top

**Risk:** Highest-recovery-cost pitfall if skipped or done incorrectly (see PITFALLS.md Pitfall 1).

### 2. App.tsx Decomposition Strategy

Current: 658 lines, all state in one `Dashboard` function. Must reach <100 lines.

**State ownership map:**
| State | Scope | Stays In |
|-------|-------|----------|
| `selectedVehicleId` | Global | AppContext or App.tsx |
| `searchTerm`, `selectedTypes` | Page-scoped (comms view) | CommunicationsView component |
| `showAddModal`, `showFilterInfo` | Component-local | Local state in trigger component |
| `isFetching`, `progress` | Feature-scoped | FetchProgress component |

**Target structure:**
```
App.tsx (~80 lines): QueryClientProvider + AppContext.Provider + AppShell
├── Sidebar.tsx: Vehicle list, selection, add trigger
├── CommunicationsView.tsx: Filter state + toolbar + content views
│   ├── Toolbar.tsx: Search + filter chips
│   └── CommunicationList.tsx (existing, unchanged for now)
├── VehicleGrid.tsx: Vehicle cards when no vehicle selected
└── StatsBar.tsx: Horizontal stat chips
```

**Key decision:** AppContext with `useReducer` for global state (no new dependency). View-local state stays in the view.

### 3. Token Audit Requirements

**Current token coverage in `index.css`:**
- Colors: Complete (primary, accent, success, warning, danger, bg layers, text, borders)
- Spacing: Complete (xs through 2xl)
- Radii: Complete (sm through full)
- Transitions: Complete (fast, default, slow)
- Typography: Font families only — missing font sizes and weights

**Gaps to fill:**
- `--font-size-xs` through `--font-size-2xl`
- `--font-weight-normal`, `--font-weight-medium`, `--font-weight-semibold`, `--font-weight-bold`
- `--sidebar-width: 240px` (layout token)
- `--header-height: 56px` (layout token)

**Hardcoded values to migrate (found in App.tsx inline styles):**
- `${typeColor}20` hex-alpha patterns → `color-mix(in srgb, var(--type-color) 12%, transparent)`
- Hardcoded `gap`, `padding`, `font-size` values in `<style>` blocks
- `color-mix()` calls with hardcoded percentages

### 4. CSS Migration Approach

Per CONTEXT.md D-08: CSS modules (`.module.css`) for scoped class names.

**Migration order:**
1. Audit tokens and fill gaps in `index.css`
2. Extract Header.tsx inline styles → `Header.module.css` (smallest component, good pilot)
3. Extract App.tsx inline styles → split across new component CSS modules
4. Verify no hardcoded hex values remain in component files

### 5. react-resizable-panels Integration

Per STACK.md: `react-resizable-panels ^4.7.6` for the sidebar + main content split.

**Phase 1 scope:** Install and wire `PanelGroup` + `Panel` + `PanelResizeHandle`. Collapse support via `collapsible` prop. Persistence via `autoSaveId`.

**Not Phase 1:** Animation on collapse (that's `motion` library, Phase 2+).

### 6. Existing Component Inventory

| Component | Lines | Inline Styles | Phase 1 Action |
|-----------|-------|--------------|----------------|
| `App.tsx` | 658 | ~200 lines | Decompose into AppShell + views |
| `Header.tsx` | ~80 | ~40 lines | Migrate to Sidebar header, CSS module |
| `CommunicationList.tsx` | ~300 | ~150 lines | Extract CSS, leave component intact |
| `VehicleCard.tsx` | ~150 | ~80 lines | Extract CSS, leave component intact |
| `AddVehicleModal.tsx` | ~120 | ~60 lines | Extract CSS, leave component intact |
| `FilterInfoModal.tsx` | ~100 | ~50 lines | Extract CSS, leave component intact |
| `FetchProgress.tsx` | ~80 | ~30 lines | Extract CSS, leave component intact |

## Validation Architecture

### Verification Approach
- **Token audit:** `grep -rn '#[0-9a-fA-F]' frontend/src/ --include='*.tsx' --include='*.css'` should return zero results outside `index.css` after migration
- **Layout shell:** Sidebar and main content scroll independently (manual test: scroll sidebar, verify main doesn't move)
- **App.tsx lines:** `wc -l frontend/src/App.tsx` should be <100
- **Prop depth:** No prop passed through more than 2 component levels (grep for prop forwarding patterns)
- **CSS modules:** All new components use `.module.css` imports

## RESEARCH COMPLETE

---
*Phase 1 research synthesized from project-level research documents*
*Researched: 2026-03-27*
