---
phase: 2
slug: communications-views
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | TypeScript compiler + ESLint + visual verification |
| **Config file** | `frontend/tsconfig.app.json`, `frontend/eslint.config.js` |
| **Quick run command** | `cd frontend && npx tsc --noEmit` |
| **Full suite command** | `cd frontend && npx tsc --noEmit && npm run lint` |
| **Estimated runtime** | ~8 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd frontend && npx tsc --noEmit`
- **After every plan wave:** Run `cd frontend && npx tsc --noEmit && npm run lint`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | VIEW-01 | build | `cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | VIEW-06, VIEW-08 | build | `cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | VIEW-01, VIEW-02, VIEW-03 | build+grep | `grep 'useReactTable' frontend/src/features/communications/components/CommunicationTable.tsx` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 2 | VIEW-04 | build | `cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | FILT-01, FILT-03 | build+grep | `grep 'FilterChips' frontend/src/features/communications/components/FilterChips.tsx` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 3 | VIEW-07 | build | `cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 02-03-02 | 03 | 3 | FILT-02, FILT-04, VIEW-05, VIEW-08 | build | `cd frontend && npx tsc --noEmit` | ❌ W0 | ⬜ pending |
| 02-03-03 | 03 | 3 | RESP-01, RESP-02, RESP-03 | grep | `grep '@media' frontend/src/features/communications/components/CommunicationsView.module.css` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `@tanstack/react-table` and `@tanstack/react-virtual` installed in `frontend/package.json`
- [ ] TypeScript compilation passes after dependency installation
