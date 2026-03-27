---
phase: 1
plan: A
title: Design Token System Completion
wave: 1
depends_on: []
files_modified:
  - frontend/src/index.css
  - frontend/package.json
  - frontend/package-lock.json
requirements_addressed: [LAYOUT-01]
autonomous: true
estimated_effort: small
---

# Plan A: Design Token System Completion

<objective>
Complete the CSS custom property token system in `index.css` with missing typography tokens, layout tokens, and install `react-resizable-panels` as the sidebar layout dependency. This is the foundation every other plan references — no component changes, just tokens and dependencies.
</objective>

## Tasks

<task id="A1">
<title>Add missing typography and layout tokens to index.css</title>
<read_first>
- frontend/src/index.css (current token system — read the full :root block)
- frontend/src/App.tsx (scan for hardcoded font-size, font-weight, gap, padding values in the style blocks)
- .planning/phases/01-foundation/01-CONTEXT.md (D-09, D-10, D-13 decisions)
- .planning/research/PITFALLS.md (Pitfall 2 — token migration warnings)
</read_first>
<action>
Add the following CSS custom properties inside the existing `:root` block in `frontend/src/index.css`, after the existing `--font-mono` declaration:

Typography size tokens:
```css
--font-size-xs: 0.6875rem;    /* 11px */
--font-size-sm: 0.75rem;      /* 12px */
--font-size-base: 0.875rem;   /* 14px — app default */
--font-size-md: 1rem;         /* 16px */
--font-size-lg: 1.125rem;     /* 18px */
--font-size-xl: 1.5rem;       /* 24px */
--font-size-2xl: 2rem;        /* 32px */
```

Typography weight tokens:
```css
--font-weight-normal: 400;
--font-weight-medium: 500;
--font-weight-semibold: 600;
--font-weight-bold: 700;
```

Layout tokens:
```css
--sidebar-width: 240px;
--sidebar-collapsed-width: 0px;
--header-height: 56px;
--toolbar-height: 48px;
```

Line height tokens:
```css
--leading-tight: 1.25;
--leading-normal: 1.5;
--leading-relaxed: 1.75;
```

Z-index tokens:
```css
--z-base: 0;
--z-sticky: 10;
--z-overlay: 20;
--z-modal: 30;
--z-toast: 40;
```
</action>
<acceptance_criteria>
- `frontend/src/index.css` contains `--font-size-xs: 0.6875rem`
- `frontend/src/index.css` contains `--font-size-base: 0.875rem`
- `frontend/src/index.css` contains `--font-weight-medium: 500`
- `frontend/src/index.css` contains `--font-weight-semibold: 600`
- `frontend/src/index.css` contains `--sidebar-width: 240px`
- `frontend/src/index.css` contains `--header-height: 56px`
- `frontend/src/index.css` contains `--toolbar-height: 48px`
- `frontend/src/index.css` contains `--z-sticky: 10`
- `frontend/src/index.css` contains `--leading-normal: 1.5`
- All new tokens are inside the existing `:root` block (not in a separate block)
- Existing tokens are NOT modified (only additions)
</acceptance_criteria>
</task>

<task id="A2">
<title>Install react-resizable-panels</title>
<read_first>
- frontend/package.json (current dependencies — verify react-resizable-panels is not already present)
- .planning/research/STACK.md (version and rationale for react-resizable-panels)
</read_first>
<action>
Run from the `frontend/` directory:
```bash
npm install react-resizable-panels
```

This installs `react-resizable-panels` (latest ^4.x) which provides `PanelGroup`, `Panel`, and `PanelResizeHandle` components for the sidebar layout in Plan B.
</action>
<acceptance_criteria>
- `frontend/package.json` contains `"react-resizable-panels"` in dependencies
- `frontend/node_modules/react-resizable-panels` directory exists
- `npm ls react-resizable-panels` exits 0 (no peer dep warnings)
- Application still compiles: `cd frontend && npx tsc --noEmit` exits 0
</acceptance_criteria>
</task>

## Verification

<must_haves>
- [ ] Token system is complete: typography sizes, weights, layout dimensions, z-indexes, line heights all defined as CSS custom properties
- [ ] No existing tokens modified — only additions
- [ ] react-resizable-panels installed and TypeScript compilation passes
- [ ] Token naming follows existing pattern (kebab-case with category prefix)
</must_haves>

---
*Plan A — Phase 01-foundation — Wave 1*
