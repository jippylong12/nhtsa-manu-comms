---
phase: 1
slug: foundation
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-03-27
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | TypeScript compiler (tsc) + grep-based audits |
| **Config file** | frontend/tsconfig.app.json |
| **Quick run command** | `cd frontend && npx tsc --noEmit` |
| **Full suite command** | `cd frontend && npx tsc --noEmit && grep -rn '#[0-9a-fA-F]' src/ --include='*.tsx' --include='*.module.css' \| grep -v 'index.css' \| wc -l` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd frontend && npx tsc --noEmit`
- **After every plan wave:** Run full suite command
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| A1 | A | 1 | LAYOUT-01 | grep | `grep '--sidebar-width' frontend/src/index.css` | N/A | ⬜ pending |
| A2 | A | 1 | LAYOUT-01 | npm | `cd frontend && npm ls react-resizable-panels` | N/A | ⬜ pending |
| B1 | B | 1 | LAYOUT-01 | file+tsc | `test -f frontend/src/components/layout/AppShell.tsx && cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| B2 | B | 1 | LAYOUT-02 | file+tsc | `test -f frontend/src/components/layout/Sidebar.tsx && cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| B3 | B | 1 | LAYOUT-02 | file+tsc | `test -f frontend/src/components/layout/SidebarVehicleItem.tsx && cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| C1 | C | 2 | LAYOUT-05 | file+tsc | `test -f frontend/src/contexts/AppContext.tsx && cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| C2 | C | 2 | LAYOUT-04 | file+tsc | `test -f frontend/src/features/communications/components/CommunicationsView.tsx` | ❌ W0 | ⬜ pending |
| C3 | C | 2 | LAYOUT-05 | file+tsc | `test -f frontend/src/features/vehicles/components/VehicleGrid.tsx` | ❌ W0 | ⬜ pending |
| C4 | C | 2 | LAYOUT-04 | file+tsc | `test -f frontend/src/components/layout/StatsBar.tsx` | ❌ W0 | ⬜ pending |
| C5 | C | 2 | LAYOUT-05 | grep | `grep -rn '<style>' frontend/src/ --include='*.tsx' \| wc -l` returns 0 | N/A | ⬜ pending |
| C6 | C | 2 | LAYOUT-05 | wc | `wc -l frontend/src/App.tsx` shows under 100 | N/A | ⬜ pending |
| C7 | C | 2 | LAYOUT-01 | grep | `grep -rn '#[0-9a-fA-F]' frontend/src/ --include='*.tsx' --include='*.module.css' \| grep -v 'index.css' \| wc -l` returns 0 | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `frontend/src/components/layout/` directory — created by Plan B
- [ ] `frontend/src/contexts/` directory — created by Plan C

*Existing infrastructure covers TypeScript compilation and linting. No new test framework needed for Phase 1 (structural/layout phase).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Sidebar scrolls independently from main content | LAYOUT-02 | Scroll interaction requires browser | Open app, add 20+ vehicles, scroll sidebar, verify main content does not scroll |
| Header and toolbar stay sticky during scroll | LAYOUT-04 | Sticky positioning requires browser | Open app, select vehicle with 50+ comms, scroll down, verify header stays at top |
| Sidebar resize drag handle works | LAYOUT-03 | Drag interaction requires browser | Open app, drag sidebar resize handle, verify sidebar width changes |
| Sidebar collapses and persists | LAYOUT-03 | Requires browser + localStorage check | Collapse sidebar, refresh page, verify sidebar stays collapsed |

---

## Validation Sign-Off

- [x] All tasks have automated verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
