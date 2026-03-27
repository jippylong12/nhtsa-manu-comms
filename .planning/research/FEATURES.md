# Feature Research

**Domain:** Data-heavy dashboard — NHTSA manufacturer communications tracker (UI/UX beautification)
**Researched:** 2026-03-27
**Confidence:** HIGH (patterns verified against Linear redesign docs, enterprise data table research, and UX pattern libraries)

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist in a professional data tool. Missing any of these makes the app feel unfinished.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Persistent sidebar with vehicle list** | Every professional data tool (Linear, Notion, GitHub) keeps primary navigation visible. Click-to-drill-back breaks flow when managing multiple vehicles. | MEDIUM | Sidebar width ~240px desktop, collapses to icon rail on tablet, off-canvas drawer on mobile. Persist collapse state in localStorage. |
| **Sticky filter/search bar** | Users scrolling 100+ comms lose context if filters scroll away. Standard behavior in Linear, Airtable, Jira. | LOW | `position: sticky; top: 0` with background fill to avoid content bleed-through. |
| **Sortable table columns** | Once in table view, users need to sort by date, type, priority. "Sort = the most-used table action" per enterprise UX research. | LOW | Date (newest first default), Type (alpha), Priority (high-first). Sort indicator (▲/▼) in header. Only one sort active at a time. |
| **Active filter summary / filter chips** | Users must see which filters are active at a glance. Hidden filter state is a trust-breaker ("why are results different?"). | LOW | Display active type filters as dismissable chips below the filter bar. "Clear all" visible when 2+ active. Already partially present as filter buttons — needs chip-style affordance. |
| **Table view for communications** | 88–621 comms per vehicle — dense table is the standard scanning mode for operational data. Card-only forces too much scroll. | HIGH | Columns: Type badge, Comm#, Summary (truncated), Date, Doc count, Keywords. Sticky header. Row click expands inline or opens detail drawer. |
| **Skeleton loaders (not just spinners)** | Expected for perceived performance on data loads. Spinners feel 2015. | LOW | Already partially present (`className="skeleton"`). Needs to match exact row height for table and card views. |
| **Inline row expansion / detail drawer** | Users need to read comm details without navigating away. Full page nav for detail = loss of list context. | MEDIUM | Current: inline expand. Target: keep inline expand for card view; for table view, add a right-side detail drawer (slide-in panel). |
| **Empty states with guidance** | When no comms match filters, users need to know why and what to do next. "No results" with no CTA is a dead end. | LOW | Three cases: (1) no vehicle selected, (2) vehicle has no data fetched yet + CTA to fetch, (3) filters too narrow + CTA to clear filters. |
| **Responsive layout (phone + tablet)** | Professional tools are used on mobile. Horizontal tables on mobile = unusable. | HIGH | Desktop: sidebar + table/card. Tablet: collapsed sidebar icon rail + table with fewer columns. Mobile: off-canvas nav + card-only view (table collapses to stacked cards). |
| **View toggle persistence** | If a user prefers table view, it should stay table view across sessions. Forcing card view on every reload is annoying. | LOW | localStorage key `comm-view-preference`. |
| **Cross-vehicle dashboard / overview** | Landing page should show aggregate value (total comms across all vehicles, recent activity) not just a list of vehicle cards. | MEDIUM | Summary stats: total vehicles, total comms, recent-30-days across all vehicles, highest-priority unreviewed items. |

---

### Differentiators (Competitive Advantage)

Features that match the Linear/Notion aesthetic and make this feel like a professional-grade tool, not a CRUD app.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Display density toggle** | Power users want more data per screen. Linear offers compact/comfortable/spacious row heights. At 600+ comms this is genuinely useful. | LOW | Three row heights: compact (32px), default (48px), spacious (64px). CSS custom property `--row-height`. Persist in localStorage. |
| **Inline comm detail drawer (right-side panel)** | Clicking a row in table view opens a slide-in panel showing full details (summary, docs, products, keywords) without leaving the list. Linear and GitHub both use this pattern. Avoids full navigation. | MEDIUM | Panel takes ~35% width on desktop. Pushes list content or overlays it. Close on Escape or clicking outside. On mobile, full-screen sheet instead. |
| **Column visibility toggle** | With 14 comm types and varying data, some columns may not be relevant for a given vehicle. Hiding irrelevant columns cleans up the table. | MEDIUM | "Columns" button opens a popover checklist. Defaults are: Type, Summary, Date, Docs. Optional: Comm#, Keywords, Products. Persist per-vehicle. |
| **Priority-grouped view** | Group comms by priority (High / Medium / Low) as collapsible sections. Surfaces critical items immediately. Better than flat-sorted list for safety-critical use. | MEDIUM | Section headers: "High Priority (12)" with collapse toggle. Default: High expanded, others collapsed. |
| **Search highlight in results** | When searching "brake", highlight "brake" in comm summary text. Linear and GitHub both do this. Makes it immediately clear why a result matched. | LOW | Use `<mark>` tags with a highlight CSS rule. Only for text search, not type filter. |
| **Keyboard navigation (Escape, arrow keys)** | Linear-class tools feel keyboard-first. Esc to close detail drawer, close modals, clear search. Future milestone scope, but CSS infrastructure should not prevent it. | LOW | Not a full keyboard shortcut system (that's out of scope per PROJECT.md), but basic Escape-to-dismiss wiring on all drawers/modals. |
| **Vehicle fetch status indicator in sidebar** | Sidebar vehicle list should show last-fetched date and a stale indicator (e.g., "2 months ago" in amber). Users need to know which data is fresh without clicking into each vehicle. | LOW | Relative date under vehicle name in sidebar. Color-coded: green (<7 days), amber (7–30), red (30+). |
| **Saved filter presets** | Users who repeatedly search "high-priority TSBs from this year" benefit from saved views. Notion and Linear both support saved filters. | HIGH | Name + type filters + date range stored in localStorage or MongoDB. Not a blocker for v1 — but architecture should not prevent adding it. |

---

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Infinite scroll for communications** | "More data = easier browsing." Seems modern. | Breaks filter chip "showing N results" mental model. Users lose their place when filters change. SSE-fetched data doesn't have cursor-based pagination from the backend — would require backend changes. | Virtualized list (only render visible rows, keep all in memory). Load up to 100 items at once (current default) which covers most vehicles. If a vehicle has 600+, increase perPage and add pagination. |
| **Real-time live updates (WebSocket polling)** | "Show new comms as they're fetched." | NHTSA data doesn't change in real-time — it's a manual fetch. SSE already handles the fetch-in-progress case. Polling would add complexity with no practical value. | Current SSE progress bar is correct. React Query staleTime handles cache freshness. No live polling needed. |
| **Theming / color picker** | "Let me customize my dark theme." | Pure scope creep for a data tool used by one person. CSS custom property approach already supports it trivially, but the UI cost is not worth it for this milestone. | Keep single polished dark theme. Refine existing token values if needed. |
| **Drag-to-reorder vehicles** | "I want my most-used vehicle first." | Adds significant interaction complexity (DnD, persistence, animation) for low-value customization. | Sort vehicles in sidebar by most recently fetched (already most-relevant ordering). |
| **Bulk actions (select + delete/archive)** | "I want to archive old comms." | Backend has no archive concept. Comms are a cache from NHTSA — archiving would require data modeling changes. Out of scope for frontend-only milestone. | Individual per-vehicle delete already works. Bulk is future work with backend support. |
| **Charts / visualization of comm trends** | "Show me a bar chart of comms by type." | Stats grid already shows counts. A chart adds visual weight without actionability — users can't *do* anything with a bar chart of TSB counts. | Keep the category stat cards (they already function as a visual breakdown and as filter shortcuts). That's the right level of visualization for this data. |

---

## Feature Dependencies

```
[Sidebar navigation layout]
    └──requires──> [App layout restructure (sidebar + main content area)]
                       └──required before──> [Responsive breakpoints for table/card/drawer]

[Table view]
    └──requires──> [Sortable column headers]
    └──requires──> [Sticky table header]
    └──enhances──> [Display density toggle]
    └──enables──> [Inline detail drawer]

[Inline detail drawer]
    └──requires──> [Table view] (primary trigger surface)
    └──conflicts with──> [Full-page navigation] (current pattern — must be replaced)

[Active filter chips]
    └──enhances──> [Existing type filter buttons] (visual upgrade, not replacement)
    └──requires──> [Clear all filter action]

[Column visibility toggle]
    └──requires──> [Table view]

[Priority-grouped view]
    └──enhances──> [Table view OR card view]
    └──requires──> [Collapsible section component]

[Cross-vehicle dashboard]
    └──requires──> [Sidebar navigation] (needs somewhere to "land" when no vehicle selected)
    └──independent from──> [Table/card view toggle] (dashboard uses its own layout)

[Vehicle fetch status indicator]
    └──requires──> [Sidebar navigation]
    └──independent from──> [Communications views]
```

### Dependency Notes

- **App layout restructure is the critical path.** Every other feature in this milestone depends on the sidebar + main content area shell existing first. Build this first.
- **Table view requires sidebar first** because the sidebar changes the available horizontal width, which determines table column strategy.
- **Detail drawer conflicts with current full-page navigation.** The current "select vehicle → full page comms view" pattern must be replaced with sidebar selection. This is a structural prerequisite, not an enhancement.
- **Filter chips enhance, not replace, the existing type filter buttons.** The filter buttons become the interaction surface; chips are the active-state summary display.

---

## MVP Definition

### Launch With (v1 — this milestone)

- [ ] **App layout restructure** — sidebar shell + main content area. Everything else is impossible without this.
- [ ] **Sidebar with vehicle list** — persistent, collapsible, with fetch status indicators.
- [ ] **Cross-vehicle dashboard** — landing view with aggregate stats when no vehicle selected.
- [ ] **Table view** — sortable by date/type, sticky header, inline row expansion.
- [ ] **Card view (improved)** — current card style but with density refinements.
- [ ] **View toggle (table ↔ card) with localStorage persistence** — essential for users with different scanning preferences.
- [ ] **Active filter chips with clear-all** — visual upgrade to current filter buttons.
- [ ] **Sticky filter/search bar** — zero-complexity win, high value at 600+ comms.
- [ ] **Inline detail drawer (right panel)** — replaces inline expand in table view.
- [ ] **Responsive breakpoints** — sidebar collapses on tablet, off-canvas on mobile, table collapses to cards on mobile.
- [ ] **Skeleton loaders** — fix existing skeletons to match actual content shape.
- [ ] **Empty states (3 variants)** — no data, no match, fetch needed.

### Add After Validation (v1.x)

- [ ] **Display density toggle** — add once table is shipping and user tests density needs.
- [ ] **Column visibility toggle** — add when table is in use and column noise becomes apparent.
- [ ] **Priority-grouped view** — add if users report difficulty finding high-priority items in flat list.
- [ ] **Search highlight in results** — low effort, add whenever touching search component.
- [ ] **Vehicle fetch status color coding** — add once sidebar is stable.

### Future Consideration (v2+)

- [ ] **Saved filter presets** — requires backend changes to persist cross-session.
- [ ] **Full keyboard shortcut system** — explicitly out of scope per PROJECT.md for this milestone.
- [ ] **Multi-vehicle comparison view** — interesting but requires new data architecture.

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| App layout restructure (sidebar shell) | HIGH | HIGH | P1 |
| Sidebar with vehicle list | HIGH | MEDIUM | P1 |
| Table view | HIGH | HIGH | P1 |
| View toggle + persistence | HIGH | LOW | P1 |
| Sticky filter bar | HIGH | LOW | P1 |
| Active filter chips | MEDIUM | LOW | P1 |
| Inline detail drawer | HIGH | MEDIUM | P1 |
| Responsive layout | HIGH | HIGH | P1 |
| Cross-vehicle dashboard | MEDIUM | MEDIUM | P1 |
| Improved empty states | MEDIUM | LOW | P1 |
| Display density toggle | MEDIUM | LOW | P2 |
| Column visibility toggle | MEDIUM | MEDIUM | P2 |
| Search highlight | MEDIUM | LOW | P2 |
| Priority-grouped view | MEDIUM | MEDIUM | P2 |
| Vehicle status indicator | LOW | LOW | P2 |
| Saved filter presets | MEDIUM | HIGH | P3 |
| Keyboard shortcut system | LOW | HIGH | P3 |

**Priority key:**
- P1: Must have for this milestone
- P2: Add in v1.x once core is shipping
- P3: Future milestone

---

## Competitor Feature Analysis

| Feature | Linear | Notion | GitHub Issues | Our Approach |
|---------|--------|--------|---------------|--------------|
| **Sidebar nav** | Persistent, collapsible to icon rail, workspace + project hierarchy | Page tree, collapsible sections | Repo sidebar, fixed | Collapsible to icon rail on tablet, off-canvas on mobile. Vehicle list with status indicators. |
| **Table view** | Full-featured table: sortable, filterable, groupable columns | Database table view with filters | Issues table: sortable date/title/status | Sortable by date/type/priority. Sticky header. Row click opens detail drawer. |
| **Card/board view** | Board view (Kanban) | Gallery view | Board view (beta) | Card list with priority left-border accent. Better than board for sequential scanning. |
| **View toggle** | Multiple views per "list" (board, table, timeline) with per-view persistence | View picker on databases | View toggle on issues list | Table + Card. Persist to localStorage. Icon button pair in toolbar. |
| **Filter UX** | Filter bar with active filter chips, "Add filter" button, save as view | Property filter chips, "Filter" button opens popover | Filter/label dropdowns in toolbar, chips shown | Existing type filter buttons evolve to chips. Priority groups remain. |
| **Information hierarchy** | Status badge left, title center-dominant, metadata right | Icon + title + metadata | State badge + title + labels + assignee | Type badge (left border color) + summary (dominant) + date + doc count. |
| **Responsive** | Desktop-first, mobile app separate | Responsive collapse, mobile app | Desktop-first, adequate mobile | Breakpoints at 768px (tablet) and 480px (mobile). Card-only on mobile. |
| **Density control** | Compact/default/spacious toggle in display settings | Not available | Not available | Display density toggle (P2, post-launch) |

---

## Sources

- [Linear UI redesign (part II)](https://linear.app/now/how-we-redesigned-the-linear-ui) — sidebar, density, visual hierarchy decisions
- [Enterprise Data Table UX Patterns — Pencil & Paper](https://www.pencilandpaper.io/articles/ux-pattern-analysis-enterprise-data-tables) — sticky headers, column management, row actions, density controls
- [Filter UX Design Patterns — Pencil & Paper](https://www.pencilandpaper.io/articles/ux-pattern-analysis-enterprise-filtering) — filter chips, active state, clear-all, 14+ category management
- [Table vs List vs Card Guide — UX Patterns for Developers](https://uxpatterns.dev/pattern-guide/table-vs-list-vs-cards) — decision framework for view types
- [Empty State UX — Pencil & Paper](https://www.pencilandpaper.io/articles/empty-states) — three empty state types
- [Best UX Practices for Sidebar — UX Planet](https://uxplanet.org/best-ux-practices-for-designing-a-sidebar-9174ee0ecaa2) — sidebar collapse patterns, state persistence
- [Responsive Data Tables for Mobile — Medium/Design Bootcamp](https://medium.com/design-bootcamp/designing-user-friendly-data-tables-for-mobile-devices-c470c82403ad) — row-to-card pattern for mobile
- [Dashboard Design Principles — UXPin](https://www.uxpin.com/studio/blog/dashboard-design-principles/) — progressive disclosure, density management

---

*Feature research for: NHTSA Comms Tracker UI/UX beautification*
*Researched: 2026-03-27*
