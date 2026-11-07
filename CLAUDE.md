## Project

**NHTSA Comms Tracker — UI/UX Beautification**

A holistic UI/UX overhaul of the NHTSA Manufacturer Communications Tracker. The app already works — it fetches, stores, filters, and displays vehicle communications from the NHTSA API. This milestone is about making it *feel* professional: rethinking layout, navigation, data presentation, and interaction patterns. The target aesthetic is Linear/Notion — clean, dense, keyboard-friendly, minimal chrome.

**Core Value:** Users can efficiently scan, filter, and consume 600+ manufacturer communications without the UI getting in the way.

### Constraints

- **Stack**: Keep React + Vite + TypeScript. No CSS framework (Tailwind, etc.) — continue with CSS custom properties approach.
- **Backend**: No backend changes in this milestone. Frontend-only.
- **Theme**: Keep existing dark theme. Refine, don't replace.
- **Compatibility**: Must work well across desktop, tablet, and mobile.

## Technology Stack

## Languages
- Python 3.10+ - Backend API and CLI tools
- TypeScript ~5.9.3 - Frontend SPA
- CSS (vanilla) - Inline styles in components + `frontend/src/index.css`, `frontend/src/App.css`
## Runtime
- Python 3.11 (detected from `__pycache__` bytecode: `cpython-311.pyc`)
- Node.js (version unspecified, no `.nvmrc`)
- pip with `pyproject.toml` (hatchling build backend) - `backend/pyproject.toml`
- npm with `package-lock.json` - `frontend/package-lock.json`
## Frameworks
| Package | Version | Purpose |
|---------|---------|---------|
| FastAPI | >=0.109.0 | Backend REST API framework |
| React | ^19.2.0 | Frontend UI library |
| Vite | ^7.2.4 | Frontend build tool and dev server |
| Package | Version | Purpose |
|---------|---------|---------|
| pytest | >=7.4.0 | Python test runner (dev dependency) |
| pytest-asyncio | >=0.23.0 | Async test support for FastAPI (dev dependency) |
| Tool | Version | Purpose |
|------|---------|---------|
| hatchling | latest | Python build backend (`backend/pyproject.toml`) |
| uvicorn[standard] | >=0.27.0 | ASGI server for FastAPI |
| black | >=24.0.0 | Python code formatter (line-length: 100, target: py310) |
| ruff | >=0.1.0 | Python linter (rules: E, F, I, W) |
| ESLint | ^9.39.1 | TypeScript/React linter |
| TypeScript | ~5.9.3 | Type checking |
## Key Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| httpx | >=0.26.0 | Async HTTP client for calling NHTSA API |
| pydantic | >=2.5.0 | Request/response schema validation |
| pydantic-settings | >=2.1.0 | Environment-based configuration (`backend/src/config.py`) |
| python-dotenv | >=1.0.0 | `.env` file loading |
| Package | Version | Purpose |
|---------|---------|---------|
| @tanstack/react-query | ^5.90.16 | Server state management, caching, SSE handling |
| date-fns | ^4.1.0 | Date formatting utilities |
| lucide-react | ^0.562.0 | Icon library |
| @vitejs/plugin-react | ^5.1.1 | React JSX transform for Vite |
## Configuration
- Backend uses `pydantic-settings` with `.env` file support (`backend/src/config.py`)
- Settings loaded via `get_settings()` with `@lru_cache` singleton pattern
- Key settings: `database_url`, `postgres_pool_max`, `nhtsa_api_base_url`, `cors_origins`, `api_host`, `api_port`, `gemini_api_key`
- Defaults are development-friendly (port 8000, Vite CORS origin). `database_url` has no default and must be set
- `backend/pyproject.toml` - Python project metadata, build config, tool settings
- `frontend/vite.config.ts` - Vite config with React plugin, `@` path alias to `./src`, API proxy to `:8000`
- `frontend/tsconfig.json` - References `tsconfig.app.json` and `tsconfig.node.json`
- `frontend/eslint.config.js` - ESLint flat config with react-hooks and react-refresh plugins
- Port 5173 (Vite default)
- Proxies `/api` requests to `http://localhost:8000` (`frontend/vite.config.ts`)
- Frontend: `@` maps to `frontend/src/` (`frontend/vite.config.ts`)
## Infrastructure
- Postgres with pgvector, at `DATABASE_URL`. Hosted on Railway; see `docs/database.md`
- Async driver: Motor (`motor.motor_asyncio`)
- Collections: `vehicles`, `communications`, `searches`
- Indexes created at startup in `backend/src/database.py`:
- Backend: Postgres is the communication cache; `existing_nhtsa_ids` is what stops sync refetching
- Frontend: TanStack Query with 5-minute stale time (`frontend/src/App.tsx`)
- Local filesystem only (no cloud storage)
## Platform Requirements
- Python 3.10+
- Node.js (recent LTS)
- Postgres with pgvector (local or remote)
- No Docker configuration detected
- Postgres with pgvector
- uvicorn ASGI server
- Static frontend build served separately or via reverse proxy

## Conventions

## Naming Patterns
- Use `snake_case.py` for all modules: `nhtsa_client.py`, `run_pipeline.py`, `flatfile.py`
- Feature modules organized as packages with `__init__.py`: `backend/src/vehicles/`, `backend/src/communications/`
- Each feature package follows: `router.py`, `service.py`, `schemas.py`
- Use `camelCase.ts` for non-component modules: `queryKeys.ts`, `api.ts`, `types.ts`
- Use `PascalCase.tsx` for React components: `VehicleCard.tsx`, `CorpusCommunicationList.tsx`, `RefreshDialog.tsx`
- Hook files prefixed with `use`: `useVehicles.ts`, `useCommunications.ts`, `useDiscovery.ts`
- Use `snake_case` for all functions and methods: `create_vehicle()`, `get_discovery_models()`
- Private/internal methods prefixed with underscore: `_build_filters()`, `_freshen_index()`, `_extract_communication()`
- Use `camelCase` for all functions: `handleAddVehicle`, `handleSearch`
- React hooks prefixed with `use`: `useVehiclesQuery`, `useCommunicationsQuery`
- API object methods use `camelCase`: `vehicleApi.list()`, `communicationApi.fetchWithProgress()`
- Use `snake_case` for all variables: `nhtsa_ids`, `id_to_summary`, `comm_count`
- Constants use `UPPER_SNAKE_CASE`: `TOP_MAKES`, `BATCH_SIZE`, `MAX_LLM_ATTEMPTS`, `CONFIDENCE_THRESHOLD`
- Use `camelCase` for local variables and state: `searchTerm`, `selectedTypes`, `vehiclesLoading`
- Constants use `UPPER_SNAKE_CASE`: `COMM_TYPE_COLORS`, `COMM_PRIORITY_TYPES`, `API_BASE`
- Use `PascalCase` for interfaces and type aliases: `Vehicle`, `Communication`, `CommType`, `FetchProgress`
- Prefix input interfaces with action: `VehicleCreate`, `VehicleUpdate`, `FetchRequest`
- Suffix response interfaces with `Response` or `ListResponse`: `VehicleResponse`, `CommunicationListResponse`
- Use `PascalCase` for Pydantic models: `VehicleCreate`, `CommunicationResponse`, `FetchResult`
- All schemas inherit from `CamelModel` base class for automatic camelCase JSON serialization
- Input schemas have no suffix, output schemas suffixed with `Response`: `VehicleCreate` vs `VehicleResponse`
## Code Style
- Formatter: `black` with line-length 100, targeting Python 3.10+
- Config location: `backend/pyproject.toml` under `[tool.black]`
- Linter: `ruff` with line-length 100
- Rules enabled: `E` (pycodestyle errors), `F` (pyflakes), `I` (isort), `W` (pycodestyle warnings)
- Config location: `backend/pyproject.toml` under `[tool.ruff]`
- Linter: `eslint` v9+ with flat config
- Config location: `frontend/eslint.config.js`
- Extends: `@eslint/js` recommended, `typescript-eslint` recommended, `react-hooks` recommended, `react-refresh` vite config
- Target: ECMAScript 2020 with browser globals
- Run command: `npm run lint` (in `frontend/`)
- `strict: true` in `frontend/tsconfig.app.json`
- `noUnusedLocals: true`, `noUnusedParameters: true`
- `noFallthroughCasesInSwitch: true`
- `verbatimModuleSyntax: true`
- Target: ES2022
## Import Organization
- `@/*` maps to `frontend/src/*` (configured in `frontend/tsconfig.app.json`)
## Error Handling
- FastAPI routers raise `HTTPException` with appropriate status codes for not-found (404) and errors (400, 500)
- Service methods return `None` or `Optional` for not-found cases; router converts to HTTP exceptions
- External API calls use try/except with specific exception types (`httpx.RequestError`, `requests.RequestException`)
- NHTSA client implements retry logic with exponential backoff for 403 (rate limiting)
- Bare `except Exception` used as catch-all fallback in data extraction functions (returns empty default)
- Custom `ApiError` class in `frontend/src/client/api.ts` wraps HTTP errors with status code
- `handleResponse<T>` generic function parses JSON or throws `ApiError`
- SSE streaming uses manual `ReadableStream` reader with error callbacks
- React Query handles retry (configured to 1 retry) and stale time (5 minutes)
## Common Patterns
- Services always call `get_database()` to get the Motor async DB instance
- Row ids are converted to `str` before returning, so the wire type does not change if the column type does
- Upsert pattern used for idempotent creates: `update_one(..., upsert=True)`
- Accepts `snake_case` Python fields, outputs `camelCase` JSON
- Shared across features via import: `from src.vehicles.schemas import CamelModel`
- Query keys managed via factory objects in `frontend/src/features/queryKeys.ts`
- Mutations invalidate relevant queries on success using `queryClient.invalidateQueries()`
- No CSS modules, Tailwind, or external stylesheets per component
- CSS custom properties (variables) used throughout: `var(--space-lg)`, `var(--color-primary)`
- Backend: `StreamingResponse` with `async def event_generator()` yielding `data: {json}\n\n`
- Frontend: Manual `ReadableStream` reader parsing SSE format with progress callbacks
- Files: `backend/src/communications/router.py` (endpoint), `frontend/src/client/communications.ts` (`fetchWithProgress`)
## API Patterns
- All routes prefixed with `/api` (mounted in `backend/src/main.py`)
- Feature routers add their own prefix: `/api/vehicles`, `/api/communications`
- Standard CRUD: `POST /`, `GET /`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`
- Discovery endpoints nested: `/api/communications/discovery/years`, `/discovery/makes`, etc.
- Input uses Pydantic models with camelCase JSON (via `CamelModel`)
- Output uses typed response models with `response_model=` parameter
- Pagination: `page` and `per_page` query params, response includes `items`, `total`, `page`, `per_page`
- 201 for creates, 204 for deletes, 200 for everything else
- 404 with `{"detail": "Vehicle not found"}` for missing resources
- 400 with `{"detail": "..."}` for bad requests
- FastAPI auto-generates 422 for validation errors
## Type System
- Full type hints on all function signatures using `typing` module
- Return types explicitly annotated: `-> dict[str, Any]`, `-> Optional[dict]`, `-> list[int]`
- Modern Python syntax: `dict[str, Any]` (not `Dict`), `list[str]` (not `List`), `str | None` (not `Optional[str]` in newer code)
- Pydantic v2 models with `Field(...)` for validation and documentation
- Strict TypeScript mode enabled
- All types centralized in `frontend/src/client/types.ts`
- Barrel export via `frontend/src/client/index.ts`
- Generic types used with React Query: `useQuery<VehicleListResponse>`
- `import type` syntax enforced for type-only imports
## Module Design
- Each feature package has `__init__.py` (currently empty)
- Routers imported directly: `from src.vehicles.router import router as vehicles_router`
- No barrel exports; direct imports throughout
- Barrel file at `frontend/src/client/index.ts` re-exports all client types and API objects
- Components export named exports (not default): `export function VehicleCard(...)`
- Exception: `App.tsx` uses `export default App`
## Comments
- Every module has a top-level docstring: `"""Business logic for Vehicles feature."""`
- Every public function/method has a docstring explaining purpose
- Inline comments for non-obvious logic: `# Upsert to handle duplicates gracefully`
- Block comments at top of files: `/* API Client - HTTP utilities for backend communication */`
- Section headers in components: `{/* Stats Summary */}`, `{/* Filters */}`
- Inline comments sparingly for clarification

## Architecture

## Pattern Overview
- Feature-based module organization on both backend and frontend
- Service layer pattern on the backend (static methods on service classes)
- Postgres as the sole data store (async via asyncpg), with pgvector for embeddings
- Server-Sent Events (SSE) for real-time fetch progress
- Vite dev server proxies `/api` to FastAPI backend at `:8000`
- camelCase JSON serialization via Pydantic alias generators (snake_case internally, camelCase over the wire)
## Component Diagram
```mermaid
```
## Layers
- Purpose: Single-page dashboard for tracking vehicles and viewing communications
- Location: `frontend/src/`
- Contains: React components, hooks, API client
- Depends on: Backend REST API
- Used by: End users via browser
- Purpose: Typed HTTP client wrapping `fetch()` calls to backend
- Location: `frontend/src/client/`
- Contains: `api.ts` (base HTTP), `vehicles.ts`, `communications.ts`, `types.ts`
- Depends on: Browser Fetch API
- Used by: React Query hooks
- Purpose: React Query wrappers for data fetching, mutations, cache invalidation
- Location: `frontend/src/features/*/hooks/`
- Contains: `useVehicles.ts`, `useCommunications.ts`, `useDiscovery.ts`
- Depends on: API client layer, TanStack React Query
- Used by: UI components
- Purpose: HTTP endpoint definitions with request validation
- Location: `backend/src/vehicles/router.py`, `backend/src/communications/router.py`
- Contains: FastAPI route handlers, query parameter definitions
- Depends on: Service layer, Pydantic schemas
- Used by: FastAPI app (`backend/src/main.py`)
- Purpose: Business logic, NHTSA data processing, Postgres operations
- Location: `backend/src/vehicles/service.py`, `backend/src/communications/service.py`
- Contains: `VehicleService`, `CommunicationService` (static method classes)
- Depends on: Database module, NHTSAClient, schemas
- Used by: Router layer
- Purpose: Async HTTP client for NHTSA government API. Discovery is Postgres-only (served from `comm_products`); the only NHTSA API call left is `/safetyIssues/byNhtsaId`, once per bulletin
- Location: `backend/src/communications/nhtsa_client.py`
- Contains: `NHTSAClient` class, response extraction helpers
- Depends on: httpx, config settings
- Used by: CommunicationService
- Purpose: Postgres connection pooling via asyncpg
- Location: `backend/src/database.py`
- Contains: Singleton `Database` class, connection lifecycle, index creation
- Depends on: Motor, config settings
- Used by: All service classes via `get_database()`
## Data Flow
- TanStack React Query handles all server state (caching, invalidation, refetching)
- Query key factory pattern in `frontend/src/features/queryKeys.ts` prevents cache bugs
- Local UI state (selected vehicle, search term, type filters) via `useState` in `App.tsx`
- No global state library (no Redux, Zustand, etc.)
## Key Abstractions
- Purpose: Pydantic BaseModel that auto-converts snake_case to camelCase for JSON serialization
- Location: `backend/src/vehicles/schemas.py`
- Pattern: All API response/request schemas inherit from `CamelModel`
- Used by: `backend/src/communications/schemas.py`, `backend/src/vehicles/schemas.py`
- Purpose: Multi-strategy classifier for NHTSA communication types (TSB, PIT, PIC, etc.)
- Location: `backend/src/communications/schemas.py` (`get_comm_type()`)
- Pattern: Priority chain: prefix-based > summary text > document type > NA format > OTHER
- Used by: `CommunicationService.fetch_and_store()`, `migrations.py`
- Purpose: Structured cache key generation for React Query
- Location: `frontend/src/features/queryKeys.ts`
- Pattern: `vehicleKeys.list(page, perPage)` returns `['vehicles', 'list', { page, perPage }]`
- Used by: All hooks for queries and cache invalidation
## Entry Points
- Location: `backend/src/main.py`
- Triggers: `uvicorn src.main:app` from the `backend/` directory
- Responsibilities: Creates FastAPI app, configures CORS, mounts the routers, disposes the Postgres pool on shutdown
- Location: `frontend/src/main.tsx`
- Triggers: `npm run dev` (Vite dev server)
- Responsibilities: Renders `<App />` into DOM root
- Location: `backend/src/communications/flatfile.py`
- Triggers: Manual `python -m src.communications.flatfile --bootstrap | --refresh | --status` (the in-app refresh also runs the `--refresh` ETag check itself)
- Responsibilities: Ingests NHTSA's flat-file corpus into `comm_products`, tracking per-window ETags in `flatfile_ingests`
- Location: `backend/src/scripts/backfill_plain_nameplate.py`
- Triggers: One-time manual `python -m src.scripts.backfill_plain_nameplate --dry-run | --apply`
- Responsibilities: Maps tracked vehicles to the plain nameplate (canonical `model`), merging identity collisions
- Location: `backend/src/migrations.py`
- Triggers: Manual `python -m src.migrations`
- Responsibilities: Backfills `communication_type` field using enhanced detection logic
## Error Handling
- Router layer raises `HTTPException` for 404/400/500 cases
- `NHTSAClient` uses retry with exponential backoff for 403 (rate limiting) and request errors
- Semaphore + 200ms delay between requests to avoid NHTSA rate limiting
- `ApiError` class in `frontend/src/client/api.ts` wraps non-OK HTTP responses
- React Query `retry: 1` for automatic single retry on failed queries
- A failed refresh run records its reason on the `refresh_runs` row, which the UI renders
## Cross-Cutting Concerns
## Postgres Tables
See `docs/schema.md` for the full schema and `backend/migrations/` for the history.

| Table | Purpose |
|-------|---------|
| `vehicles` | Tracked vehicles, identified by `(year, make, model)` (canonical plain nameplate). Soft-deleted via `active` |
| `communications` | One row per `nhtsa_id`. `status` is the contract between sync and the processor |
| `comm_documents` | One row per PDF: extracted text, LLM output, embedding |
| `communication_vehicles` | The join that removes the per-vehicle duplication |
| `comm_products` | Flat-file index: one row per (communication, make, model, year); what discovery and sync read |
| `flatfile_ingests` | ETag ledger per flat-file window |
| `refresh_runs` | State of an in-app refresh; one active at a time |
| `pipeline_runs` | Operational record of a CLI job execution |
| `usage_events` | Billing record: one row per billable model call |

## Project Management

Use Linear as the source of truth for planning and tracking work. Start substantive work through `/linear-project-workflow` (projects, milestones, issues) rather than ad-hoc planning artifacts.
