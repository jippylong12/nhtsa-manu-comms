# Coding Conventions

**Analysis Date:** 2026-03-27

## Naming Patterns

**Files (Backend - Python):**
- Use `snake_case.py` for all modules: `cache_utils.py`, `http_utils.py`, `nhtsa_client.py`
- Feature modules organized as packages with `__init__.py`: `backend/src/vehicles/`, `backend/src/communications/`
- Each feature package follows: `router.py`, `service.py`, `schemas.py`

**Files (Frontend - TypeScript/React):**
- Use `camelCase.ts` for non-component modules: `queryKeys.ts`, `api.ts`, `types.ts`
- Use `PascalCase.tsx` for React components: `VehicleCard.tsx`, `CommunicationList.tsx`, `Header.tsx`
- Hook files prefixed with `use`: `useVehicles.ts`, `useCommunications.ts`, `useDiscovery.ts`

**Functions (Backend):**
- Use `snake_case` for all functions and methods: `create_vehicle()`, `fetch_details_with_cache()`
- Private/internal methods prefixed with underscore: `_product_matches()`, `_matches_keywords()`, `_extract_communication()`

**Functions (Frontend):**
- Use `camelCase` for all functions: `handleAddVehicle`, `handleSearch`
- React hooks prefixed with `use`: `useVehiclesQuery`, `useCommunicationsQuery`
- API object methods use `camelCase`: `vehicleApi.list()`, `communicationApi.fetchWithProgress()`

**Variables (Backend):**
- Use `snake_case` for all variables: `nhtsa_ids`, `id_to_summary`, `comm_count`
- Constants use `UPPER_SNAKE_CASE`: `MAX_WORKERS`, `CACHE_DIR`, `TARGET_YEAR`, `TOP_MAKES`

**Variables (Frontend):**
- Use `camelCase` for local variables and state: `searchTerm`, `selectedTypes`, `vehiclesLoading`
- Constants use `UPPER_SNAKE_CASE`: `COMM_TYPE_COLORS`, `COMM_PRIORITY_TYPES`, `API_BASE`

**Types (Frontend):**
- Use `PascalCase` for interfaces and type aliases: `Vehicle`, `Communication`, `CommType`, `FetchProgress`
- Prefix input interfaces with action: `VehicleCreate`, `VehicleUpdate`, `FetchRequest`
- Suffix response interfaces with `Response` or `ListResponse`: `VehicleResponse`, `CommunicationListResponse`

**Types (Backend - Pydantic):**
- Use `PascalCase` for Pydantic models: `VehicleCreate`, `CommunicationResponse`, `FetchResult`
- All schemas inherit from `CamelModel` base class for automatic camelCase JSON serialization
- Input schemas have no suffix, output schemas suffixed with `Response`: `VehicleCreate` vs `VehicleResponse`

## Code Style

**Formatting (Backend):**
- Formatter: `black` with line-length 100, targeting Python 3.10+
- Config location: `backend/pyproject.toml` under `[tool.black]`

**Linting (Backend):**
- Linter: `ruff` with line-length 100
- Rules enabled: `E` (pycodestyle errors), `F` (pyflakes), `I` (isort), `W` (pycodestyle warnings)
- Config location: `backend/pyproject.toml` under `[tool.ruff]`

**Linting (Frontend):**
- Linter: `eslint` v9+ with flat config
- Config location: `frontend/eslint.config.js`
- Extends: `@eslint/js` recommended, `typescript-eslint` recommended, `react-hooks` recommended, `react-refresh` vite config
- Target: ECMAScript 2020 with browser globals
- Run command: `npm run lint` (in `frontend/`)

**TypeScript Strictness:**
- `strict: true` in `frontend/tsconfig.app.json`
- `noUnusedLocals: true`, `noUnusedParameters: true`
- `noFallthroughCasesInSwitch: true`
- `verbatimModuleSyntax: true`
- Target: ES2022

**No Prettier configured.** Formatting relies on ESLint for frontend, Black for backend.

## Import Organization

**Backend Python:**
1. Standard library imports (`asyncio`, `datetime`, `typing`, `os`, `threading`)
2. Third-party imports (`requests`, `httpx`, `fastapi`, `pydantic`, `bson`, `motor`)
3. Local imports using `src.` prefix for backend package: `from src.config import get_settings`
4. Root-level CLI scripts use direct imports: `from config import TARGET_YEAR`

**Frontend TypeScript:**
1. React/library imports (`react`, `@tanstack/react-query`, `lucide-react`)
2. Local imports using `@/` path alias (maps to `src/`)
3. Type-only imports use `import type { ... }` syntax (enforced by `verbatimModuleSyntax`)

**Path Aliases:**
- `@/*` maps to `frontend/src/*` (configured in `frontend/tsconfig.app.json`)

## Error Handling

**Backend API Patterns:**
- FastAPI routers raise `HTTPException` with appropriate status codes for not-found (404) and errors (400, 500)
- Service methods return `None` or `Optional` for not-found cases; router converts to HTTP exceptions
- External API calls use try/except with specific exception types (`httpx.RequestError`, `requests.RequestException`)
- NHTSA client implements retry logic with exponential backoff for 403 (rate limiting)
- Bare `except Exception` used as catch-all fallback in data extraction functions (returns empty default)

**Frontend Patterns:**
- Custom `ApiError` class in `frontend/src/client/api.ts` wraps HTTP errors with status code
- `handleResponse<T>` generic function parses JSON or throws `ApiError`
- SSE streaming uses manual `ReadableStream` reader with error callbacks
- React Query handles retry (configured to 1 retry) and stale time (5 minutes)

**Pattern: Return empty defaults on failure:**
```python
# Backend pattern - return empty list/dict on parse failure
try:
    comms = details["results"][0]["safetyIssues"]["manufacturerCommunications"]
except (KeyError, IndexError, TypeError):
    return []
```

## Common Patterns

**Backend Service Layer Pattern:**
Each feature uses a static service class with `@staticmethod` async methods:
```python
# Pattern in backend/src/vehicles/service.py, backend/src/communications/service.py
class VehicleService:
    @staticmethod
    async def create_vehicle(...) -> dict[str, Any]:
        db = get_database()
        # ... business logic
```
- Services always call `get_database()` to get the Motor async DB instance
- MongoDB `_id` (ObjectId) is converted to `str` before returning: `doc["_id"] = str(doc["_id"])`
- Upsert pattern used for idempotent creates: `update_one(..., upsert=True)`

**Backend CamelModel Base Class:**
All Pydantic schemas inherit from `CamelModel` (defined in `backend/src/vehicles/schemas.py`):
```python
class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
```
- Accepts `snake_case` Python fields, outputs `camelCase` JSON
- Shared across features via import: `from src.vehicles.schemas import CamelModel`

**Frontend API Client Pattern:**
API functions are grouped as const objects per domain:
```typescript
// Pattern in frontend/src/client/vehicles.ts, communications.ts
export const vehicleApi = {
    list: (page = 1, perPage = 20) =>
        request<VehicleListResponse>(`/vehicles?page=${page}&per_page=${perPage}`),
    create: (data: VehicleCreate) =>
        request<Vehicle>('/vehicles', { method: 'POST', body: JSON.stringify(data) }),
};
```

**Frontend React Query Hook Pattern:**
Each feature has a hooks file exporting query/mutation hooks:
```typescript
// Pattern in frontend/src/features/vehicles/hooks/useVehicles.ts
export function useVehiclesQuery(page = 1, perPage = 20) {
    return useQuery({
        queryKey: vehicleKeys.list(page, perPage),
        queryFn: () => vehicleApi.list(page, perPage),
    });
}
```
- Query keys managed via factory objects in `frontend/src/features/queryKeys.ts`
- Mutations invalidate relevant queries on success using `queryClient.invalidateQueries()`

**Frontend Inline CSS Pattern:**
Components use inline `<style>` blocks within JSX (CSS-in-JSX):
```tsx
<style>{`
  .vehicle-banner { display: flex; ... }
  @media (max-width: 768px) { ... }
`}</style>
```
- No CSS modules, Tailwind, or external stylesheets per component
- CSS custom properties (variables) used throughout: `var(--space-lg)`, `var(--color-primary)`

**SSE (Server-Sent Events) Pattern:**
Long-running fetch operations use SSE streaming:
- Backend: `StreamingResponse` with `async def event_generator()` yielding `data: {json}\n\n`
- Frontend: Manual `ReadableStream` reader parsing SSE format with progress callbacks
- Files: `backend/src/communications/router.py` (endpoint), `frontend/src/client/communications.ts` (`fetchWithProgress`)

## API Patterns

**REST Endpoints:**
- All routes prefixed with `/api` (mounted in `backend/src/main.py`)
- Feature routers add their own prefix: `/api/vehicles`, `/api/communications`
- Standard CRUD: `POST /`, `GET /`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`
- Discovery endpoints nested: `/api/communications/discovery/years`, `/discovery/makes`, etc.

**Request/Response:**
- Input uses Pydantic models with camelCase JSON (via `CamelModel`)
- Output uses typed response models with `response_model=` parameter
- Pagination: `page` and `per_page` query params, response includes `items`, `total`, `page`, `per_page`
- 201 for creates, 204 for deletes, 200 for everything else

**Error Responses:**
- 404 with `{"detail": "Vehicle not found"}` for missing resources
- 400 with `{"detail": "..."}` for bad requests
- FastAPI auto-generates 422 for validation errors

## Type System

**Backend:**
- Full type hints on all function signatures using `typing` module
- Return types explicitly annotated: `-> dict[str, Any]`, `-> Optional[dict]`, `-> list[int]`
- Modern Python syntax: `dict[str, Any]` (not `Dict`), `list[str]` (not `List`), `str | None` (not `Optional[str]` in newer code)
- Pydantic v2 models with `Field(...)` for validation and documentation

**Frontend:**
- Strict TypeScript mode enabled
- All types centralized in `frontend/src/client/types.ts`
- Barrel export via `frontend/src/client/index.ts`
- Generic types used with React Query: `useQuery<VehicleListResponse>`
- `import type` syntax enforced for type-only imports

## Module Design

**Backend Exports:**
- Each feature package has `__init__.py` (currently empty)
- Routers imported directly: `from src.vehicles.router import router as vehicles_router`
- No barrel exports; direct imports throughout

**Frontend Exports:**
- Barrel file at `frontend/src/client/index.ts` re-exports all client types and API objects
- Components export named exports (not default): `export function VehicleCard(...)`
- Exception: `App.tsx` uses `export default App`

## Comments

**Docstrings (Backend):**
- Every module has a top-level docstring: `"""Business logic for Vehicles feature."""`
- Every public function/method has a docstring explaining purpose
- Inline comments for non-obvious logic: `# Upsert to handle duplicates gracefully`

**Comments (Frontend):**
- Block comments at top of files: `/* API Client - HTTP utilities for backend communication */`
- Section headers in components: `{/* Stats Summary */}`, `{/* Filters */}`
- Inline comments sparingly for clarification

---

*Convention analysis: 2026-03-27*
