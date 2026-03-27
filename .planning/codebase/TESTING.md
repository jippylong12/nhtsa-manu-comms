# Testing

## Test Framework

No test framework is currently configured for either the backend (Python/FastAPI) or frontend (React/Vite).

## Test Structure

No test files exist in the project source directories. All `*test*` and `*spec*` matches are within `node_modules/` or `venv/` (third-party packages).

## Test Coverage

| Area | Coverage | Notes |
|------|----------|-------|
| Backend API endpoints | None | No test files found |
| Backend services (comms, processing, cache) | None | No unit or integration tests |
| Frontend components | None | No component tests |
| Frontend hooks/queries | None | No hook tests |
| E2E flows | None | No E2E test setup (Playwright, Cypress, etc.) |

## How to Run Tests

No test commands are configured:
- **Backend:** No `pytest` in requirements, no test configuration
- **Frontend:** `package.json` does not define a `test` script; no vitest/jest config found

## Recommendations

### Backend (Python/FastAPI)
- Add `pytest` + `httpx` for API endpoint testing
- Add `pytest-asyncio` for async test support
- Structure: `backend/tests/` with `test_*.py` files
- Priority areas: NHTSA API integration, communication type detection, vehicle catalog queries

### Frontend (React/Vite)
- Add `vitest` + `@testing-library/react` for component/hook testing
- Structure: colocated `*.test.tsx` files or `__tests__/` directories
- Priority areas: TanStack Query hooks, communication type rendering, search/filter logic

### E2E
- Consider Playwright for critical user flows (vehicle lookup → communications display)
