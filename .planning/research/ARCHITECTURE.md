# Architecture Research

**Domain:** React dashboard — sidebar nav, view modes, component library, data tool UI
**Researched:** 2026-03-27
**Confidence:** HIGH

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        AppShell (layout root)                     │
│  ┌────────────────┐  ┌────────────────────────────────────────┐  │
│  │   Sidebar      │  │            Main Content Area            │  │
│  │  ─────────── │  │  ┌──────────────────────────────────┐   │  │
│  │  Logo/Brand   │  │  │         Page Header               │   │  │
│  │  Nav Items    │  │  │  (title, actions, view toggle)    │   │  │
│  │  ─────────── │  │  └──────────────────────────────────┘   │  │
│  │  Vehicle List │  │  ┌──────────────────────────────────┐   │  │
│  │  (scrollable) │  │  │         Toolbar / Filters         │   │  │
│  │  ─────────── │  │  └──────────────────────────────────┘   │  │
│  │  Footer links │  │  ┌──────────────────────────────────┐   │  │
│  └────────────────┘  │  │     View (Table or Card grid)    │   │  │
│                       │  └──────────────────────────────────┘   │  │
│                       └────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `AppShell` | Layout grid (sidebar + main), responsive collapse | `Sidebar`, `<Outlet>` / page slot |
| `Sidebar` | Vehicle list, active selection, add vehicle trigger | `useVehicles`, router/nav state |
| `SidebarVehicleItem` | Single vehicle row — name, count badge, fetch status | `Sidebar` (props down) |
| `PageHeader` | Page title, breadcrumb-style context, primary action button | Page-level component |
| `Toolbar` | Search input, filter chips, view toggle | Filter state (local or context) |
| `ViewToggle` | Table / Card switch with localStorage persistence | `Toolbar` |
| `CommunicationTable` | Dense sortable table for 600+ comms | `CommunicationList` replacement |
| `CommunicationCardGrid` | Improved card layout for comms | `CommunicationList` replacement |
| `CommunicationRow` | Expandable table row (already exists, extract) | `CommunicationTable` |
| `StatsBar` | Horizontal stat chips above comm view | `useVehicleStatsQuery` |
| `FilterChips` | Active filter pills with remove action | Filter state |
| `FetchProgressBar` | SSE progress overlay (already exists) | `useFetchCommunications` |
| `AddVehicleModal` | Vehicle creation form (already exists) | `useCreateVehicle` |
| `FilterInfoModal` | Filter help content (already exists) | Local open/close state |
| `OverviewDashboard` | Cross-vehicle stats and landing screen | `useVehiclesQuery`, `useVehicleStatsQuery` |

---

## Recommended Project Structure

```
frontend/src/
├── components/                  # Design system — shared across all features
│   ├── layout/
│   │   ├── AppShell.tsx         # Top-level layout grid
│   │   ├── Sidebar.tsx          # Sidebar shell + vehicle list
│   │   └── SidebarVehicleItem.tsx
│   ├── primitives/              # Smallest reusable atoms
│   │   ├── Badge.tsx
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   ├── Skeleton.tsx
│   │   └── Tooltip.tsx
│   ├── composite/               # Assembled from primitives
│   │   ├── EmptyState.tsx
│   │   ├── FilterChips.tsx
│   │   ├── PageHeader.tsx
│   │   ├── SearchInput.tsx
│   │   ├── StatCard.tsx
│   │   ├── Toolbar.tsx
│   │   └── ViewToggle.tsx
│   └── modals/
│       ├── FilterInfoModal.tsx  # moved from components/
│       └── ConfirmModal.tsx     # replace window.confirm
├── features/
│   ├── communications/
│   │   ├── components/
│   │   │   ├── CommunicationCardGrid.tsx   # card view
│   │   │   ├── CommunicationTable.tsx      # table view
│   │   │   ├── CommunicationRow.tsx        # extracted from CommunicationList
│   │   │   ├── CommunicationDetail.tsx     # expanded content panel
│   │   │   └── FetchProgress.tsx           # unchanged
│   │   └── hooks/                          # unchanged
│   ├── vehicles/
│   │   ├── components/
│   │   │   ├── VehicleCard.tsx             # unchanged (used in overview)
│   │   │   └── AddVehicleModal.tsx         # unchanged
│   │   └── hooks/                          # unchanged
│   └── overview/
│       └── components/
│           └── OverviewDashboard.tsx       # new cross-vehicle landing
├── styles/
│   ├── index.css                # global reset, base tokens (unchanged)
│   ├── layout.css               # AppShell, Sidebar, page grid
│   ├── components.css           # Extracted from inline <style> tags
│   └── tokens.css               # Re-export of :root vars (optional separate file)
├── hooks/
│   └── useViewPreference.ts     # localStorage-backed table/card toggle
├── client/                      # unchanged API layer
└── App.tsx                      # slimmed: providers + AppShell only
```

### Structure Rationale

- **`components/layout/`:** Layout shells are not feature-specific. They live here so any page can use them without feature coupling.
- **`components/primitives/`:** The atoms Button, Badge, Input, Skeleton are already partially defined as CSS classes in `index.css`. Wrapping them as React components gives typed props, enforces consistent usage, and documents the design system.
- **`components/composite/`:** Assembled UI that appears in multiple contexts (PageHeader shows on both vehicle overview and comms view; Toolbar hosts both views' filters).
- **`features/`:** Feature code stays feature-owned. Hooks are not touched in this milestone — only components change.
- **`styles/`:** CSS migrates out of `<style>` tags into files. `index.css` keeps the tokens and reset. `layout.css` and `components.css` get the extracted inline styles. No naming collision risk since class names are already established.
- **`hooks/useViewPreference`:** One hook, outside features, because the toggle is cross-concern UI state.

---

## Architectural Patterns

### Pattern 1: Layout Shell (AppShell)

**What:** A single top-level component that owns the grid: sidebar column + scrollable main column. All pages render into the main slot. The shell never contains business logic.

**When to use:** Any app with persistent chrome (sidebar, topbar) that frames changing content.

**Trade-offs:** Simple and direct for this app's scope. For a multi-route app you'd pair with React Router's `<Outlet>` — but since this app has no router yet, the shell can accept a `children` prop and the parent decides what renders.

```typescript
// AppShell.tsx — pure layout, no data
interface AppShellProps {
  sidebar: React.ReactNode;
  children: React.ReactNode;
  sidebarCollapsed?: boolean;
}

export function AppShell({ sidebar, children, sidebarCollapsed }: AppShellProps) {
  return (
    <div className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="app-sidebar">{sidebar}</aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
```

CSS drives the layout grid — the component just applies class names. This keeps the shell trivially testable and the layout customizable without touching React.

### Pattern 2: Controlled View Toggle with Persistence

**What:** A `useViewPreference` hook reads/writes `localStorage` and returns `[view, setView]`. The `ViewToggle` component is a pure presenter that accepts these as props.

**When to use:** Any UI preference that should survive page refresh but doesn't need server persistence.

**Trade-offs:** Simple, zero dependencies. Downside: not synced across tabs (acceptable for this app).

```typescript
// hooks/useViewPreference.ts
type ViewMode = 'table' | 'card';

export function useViewPreference(key = 'comms-view'): [ViewMode, (v: ViewMode) => void] {
  const [view, setViewState] = useState<ViewMode>(
    () => (localStorage.getItem(key) as ViewMode) ?? 'table'
  );
  const setView = (v: ViewMode) => {
    localStorage.setItem(key, v);
    setViewState(v);
  };
  return [view, setView];
}
```

### Pattern 3: CSS Custom Properties for Component Variants

**What:** Instead of inline `style={{ color: typeColor }}`, encode variants as CSS custom property overrides. The component sets `--type-color` on its root element; all descendant rules reference it.

**When to use:** When a component has a dynamic color that applies to multiple sub-elements (border, background, text). Already partially used with `--type-color` on filter buttons.

**Trade-offs:** Keeps JSX clean. Requires discipline to not spread to unrelated CSS.

```typescript
// Usage in CommunicationRow
<div
  className="comm-row"
  style={{ '--type-color': typeColor } as React.CSSProperties}
>
  {/* .comm-row border, badge bg, dot color all use var(--type-color) in CSS */}
```

### Pattern 4: Extract Inline Styles to Co-located CSS Files

**What:** Each component's `<style>{``...``}</style>` block moves into a `.css` file with the same name (e.g., `CommunicationRow.css`), imported at the top of the component. No CSS modules — just regular class names matching the existing convention.

**When to use:** Immediately, for every component being touched in this milestone.

**Trade-offs:** No scoping (class names are global), but the existing naming convention (`comm-row`, `vehicle-banner`, etc.) is already BEM-adjacent and collision-safe. CSS modules would require renaming every class — not worth the churn for this codebase.

```typescript
// Before
function CommunicationRow() {
  return (
    <>
      <div className="comm-row">...</div>
      <style>{` .comm-row { ... } `}</style>
    </>
  );
}

// After
import './CommunicationRow.css';   // ← extracted
function CommunicationRow() {
  return <div className="comm-row">...</div>;
}
```

### Pattern 5: Sidebar Active State via Lifted State (not Router)

**What:** Since there's no React Router in place, the selected vehicle ID lives in `App.tsx` (already does). The Sidebar receives `selectedVehicleId` and `onSelectVehicle` as props. No routing refactor needed.

**When to use:** App is currently single-page, single-route. Adding a router is out of scope for this milestone.

**Trade-offs:** Prop drilling from App down to Sidebar → SidebarVehicleItem is shallow (2 levels), acceptable. If a third level of drilling appears, lift to a context.

---

## Data Flow

### View Selection Flow

```
User clicks vehicle in Sidebar
    ↓
onSelectVehicle(vehicleId) — prop callback
    ↓
App.tsx: setSelectedVehicleId(vehicleId)
    ↓
Dashboard re-renders with selectedVehicleId
    ↓
useCommunicationsQuery(filters) fires
    ↓
CommunicationTable or CommunicationCardGrid receives comms[]
```

### View Toggle Flow

```
User clicks table/card button in ViewToggle
    ↓
setView('table' | 'card') from useViewPreference
    ↓
localStorage updated
    ↓
view state updates → Toolbar re-renders
    ↓
Dashboard renders <CommunicationTable> or <CommunicationCardGrid>
```

### Filter State Flow

```
Filter state lives in Dashboard (parent of Toolbar + view)
    ↓
Toolbar receives: searchTerm, setSearchTerm, selectedTypes, setSelectedTypes
    ↓
useCommunicationsQuery(filters) reacts to filter changes via useMemo
    ↓
View component receives filtered comms[]
    ↓
No filter state lives inside view components — they are pure presenters
```

### Key Data Flows

1. **Sidebar → Content area:** `selectedVehicleId` is the only shared state between sidebar and main content. It lives in `App.tsx` (or a thin context if drilling gets deep).
2. **Stats → Filter chips:** `useVehicleStatsQuery` provides category counts. `StatsBar` and `Toolbar` both read from the same query cache — no duplication.
3. **SSE Progress:** `FetchProgressBar` is positioned inside the main content area, above the comm list. It does not affect the sidebar.

---

## Component Build Order

Build in this order to avoid blocked work:

```
1. CSS extraction (no component changes — just move styles out of <style> tags)
   └── index.css stays, component styles → component.css files

2. Primitive components (no data deps, just wrapping existing CSS classes)
   ├── Button.tsx
   ├── Badge.tsx
   ├── Input.tsx
   └── Skeleton.tsx

3. AppShell + Sidebar (layout only, uses useVehicles hook which already exists)
   ├── AppShell.tsx
   ├── Sidebar.tsx
   └── SidebarVehicleItem.tsx

4. ViewToggle + useViewPreference (no data deps)
   ├── hooks/useViewPreference.ts
   └── ViewToggle.tsx

5. CommunicationTable (replaces CommunicationList in table mode)
   └── CommunicationTable.tsx (CommunicationRow already extracted from list)

6. Toolbar refactor (search + filters + ViewToggle assembled)
   └── Toolbar.tsx

7. StatsBar extraction (stats are already fetched, just extract render)
   └── StatsBar.tsx

8. OverviewDashboard (new screen — cross-vehicle landing)
   └── OverviewDashboard.tsx

9. CommunicationCardGrid (improved card view — replaces card path in list)
   └── CommunicationCardGrid.tsx

10. PageHeader composite
    └── PageHeader.tsx
```

Dependencies: Steps 1–4 have no blockers. Step 5 blocks nothing else but enables a testable core loop early. Steps 8–10 can be parallelized.

---

## Design System Extraction Plan

The existing `index.css` is well-structured. The inline `<style>` tags in components are the only technical debt. Strategy:

| Source | What's There | Move To |
|--------|-------------|---------|
| `App.tsx` (comms view styles) | `.vehicle-banner`, `.stats-grid`, `.filters-bar`, `.type-filter-btn`, etc. | `features/communications/components/CommunicationView.css` |
| `App.tsx` (vehicles view styles) | `.hero-section`, `.vehicles-grid` | `features/vehicles/components/VehicleGrid.css` |
| `Header.tsx` | `.header`, `.logo`, `.nav-links` | `components/layout/Sidebar.css` (header moves into sidebar) |
| `CommunicationList.tsx` | `.comm-row`, `.comm-header`, `.comm-summary`, etc. | `features/communications/components/CommunicationRow.css` |
| `VehicleCard.tsx` | All vehicle card styles | `features/vehicles/components/VehicleCard.css` |
| `AddVehicleModal.tsx` | Modal styles | `components/modals/Modal.css` (shared modal chrome) |

The `:root` token block in `index.css` is the design system foundation — do not move it. The table and button classes in `index.css` are already component-level utilities; they get wrapped as React components in step 2.

---

## Anti-Patterns

### Anti-Pattern 1: Putting Layout in Feature Components

**What people do:** Build sidebar vehicle list directly inside `CommunicationList` or `VehicleCard`, mixing layout chrome with data presentation.

**Why it's wrong:** Layout decisions (sidebar width, collapse behavior, responsive breakpoints) should be owned by one place. Feature components become hard to reuse or test in isolation.

**Do this instead:** `AppShell` owns the grid. `Sidebar` owns the vehicle list. Features render inside the main slot and have no knowledge of sidebar existence.

### Anti-Pattern 2: Duplicating Filter State in Multiple Components

**What people do:** `Toolbar`, `StatsBar`, and `CommunicationTable` each manage their own filter slice.

**Why it's wrong:** State synchronization bugs — clicking a stat chip doesn't clear the toolbar search. The comms view in `App.tsx` already has this problem at small scale.

**Do this instead:** All filter state lives in one place (Dashboard or a `useCommFilters` hook). Components receive state and callbacks as props. They are pure presenters.

### Anti-Pattern 3: Replacing `<style>` Tags with Inline Style Objects

**What people do:** Convert `<style>{`.foo { color: red }`}</style>` to `style={{ color: 'red' }}` on elements.

**Why it's wrong:** Loses pseudo-selectors (`:hover`, `:focus`), media queries, and state-based class toggling. Performance is worse at scale.

**Do this instead:** Extract to `.css` files. Keep class names. Use CSS custom properties for dynamic values.

### Anti-Pattern 4: Adding React Router Just for the Sidebar

**What people do:** Introduce React Router to handle vehicle selection as a URL route (`/vehicles/:id/comms`).

**Why it's wrong:** Adds significant complexity (route config, `useParams`, programmatic navigation) for what is currently a single-page state transition. Deep linking to a vehicle isn't a stated requirement.

**Do this instead:** Keep `selectedVehicleId` in App-level state. If deep linking becomes a requirement in a future milestone, add the router then with full intent.

### Anti-Pattern 5: Creating a New CSS Framework

**What people do:** When extracting inline styles, introduce Tailwind or CSS Modules "just to do it right."

**Why it's wrong:** The existing custom property system is clean and complete. A framework migration is a full rewrite of every class name — enormous churn with no functional benefit.

**Do this instead:** Extract inline styles to plain `.css` files. Keep the existing token names and class naming convention. The design system is the `:root` block in `index.css`.

---

## Integration Points

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|--------------|-------|
| `AppShell` ↔ `Sidebar` | Props: `vehicles`, `selectedId`, `onSelect`, `onAdd` | Sidebar is a controlled component |
| `Sidebar` ↔ `useVehicles` | Hook called directly inside Sidebar | Sidebar owns its own data fetching |
| `Dashboard` ↔ `Toolbar` | Props: filter state + setters | Dashboard is the filter state owner |
| `Dashboard` ↔ view components | Props: `communications[]`, `isLoading` | Views are pure presenters |
| `CommunicationTable` / `CommunicationCardGrid` ↔ `CommunicationRow` | Props: `comm`, `occurrence` | Row is shared between both views |

### Responsive Behavior

| Breakpoint | Sidebar | Main Content |
|-----------|---------|-------------|
| Desktop (>1024px) | Visible, fixed width 260px | Full width, scrollable |
| Tablet (640–1024px) | Collapsible (icon-only mode) | Full width |
| Mobile (<640px) | Hidden, toggle button shows overlay | Full screen |

The AppShell CSS grid handles this with a single class toggle (`sidebar-collapsed`). No JavaScript layout logic.

---

## Sources

- Linear app UI patterns: observed behavior (no public source)
- React component composition patterns: HIGH confidence, standard React docs
- CSS custom properties for theming: HIGH confidence, MDN / existing codebase pattern
- localStorage persistence pattern: HIGH confidence, well-established
- Layout shell pattern: HIGH confidence, common in Linear/Notion-style data tools

---

*Architecture research for: React dashboard sidebar + view modes + component library*
*Researched: 2026-03-27*
