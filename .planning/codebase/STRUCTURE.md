# Codebase Structure

**Analysis Date:** 2026-03-27

## Directory Layout

```
nhtsa-manu-comms/
├── backend/                    # Python FastAPI backend
│   ├── src/                    # Application source code
│   │   ├── communications/     # Communications feature module
│   │   │   ├── __init__.py
│   │   │   ├── nhtsa_client.py # NHTSA API client (httpx)
│   │   │   ├── router.py       # FastAPI routes
│   │   │   ├── schemas.py      # Pydantic models + type detection
│   │   │   └── service.py      # Business logic
│   │   ├── vehicles/           # Vehicles feature module
│   │   │   ├── __init__.py
│   │   │   ├── router.py       # FastAPI routes
│   │   │   ├── schemas.py      # Pydantic models + CamelModel base
│   │   │   └── service.py      # Business logic
│   │   ├── scripts/            # One-off scripts
│   │   │   ├── __init__.py
│   │   │   └── seed_vehicle_catalog.py
│   │   ├── __init__.py
│   │   ├── config.py           # Pydantic Settings (env vars)
│   │   ├── database.py         # MongoDB connection (Motor)
│   │   ├── main.py             # FastAPI app entry point
│   │   └── migrations.py       # Data backfill scripts
│   ├── tools/                  # Empty directory
│   ├── venv/                   # Python virtualenv
│   ├── .env                    # Environment variables (not committed)
│   └── pyproject.toml          # Python project config
├── frontend/                   # React + TypeScript frontend
│   ├── src/
│   │   ├── client/             # API client layer
│   │   │   ├── api.ts          # Base HTTP utilities
│   │   │   ├── communications.ts # Communication API functions
│   │   │   ├── index.ts        # Barrel export
│   │   │   ├── types.ts        # TypeScript interfaces (mirrors backend schemas)
│   │   │   └── vehicles.ts     # Vehicle API functions
│   │   ├── contexts/           # React context providers
│   │   │   └── AppContext.tsx       # Global UI state (useReducer)
│   │   ├── components/         # Shared UI components
│   │   │   ├── layout/             # Layout components
│   │   │   │   ├── AppShell.tsx     # Two-column layout shell
│   │   │   │   ├── AppShell.module.css
│   │   │   │   ├── Sidebar.tsx      # Vehicle list sidebar
│   │   │   │   ├── Sidebar.module.css
│   │   │   │   ├── SidebarVehicleItem.tsx
│   │   │   │   ├── SidebarVehicleItem.module.css
│   │   │   │   ├── StatsBar.tsx     # Reusable stat chips
│   │   │   │   └── StatsBar.module.css
│   │   │   ├── FilterInfoModal.tsx  # Filter help modal
│   │   │   ├── FilterInfoModal.module.css
│   │   │   ├── Header.tsx           # App header
│   │   │   └── Header.module.css
│   │   ├── features/           # Feature-organized code
│   │   │   ├── communications/
│   │   │   │   ├── components/
│   │   │   │   │   ├── CommunicationList.tsx       # Comm list view
│   │   │   │   │   ├── CommunicationList.module.css
│   │   │   │   │   ├── CommunicationsView.tsx      # Full comms page (filters, stats, list)
│   │   │   │   │   ├── CommunicationsView.module.css
│   │   │   │   │   ├── FetchProgress.tsx           # SSE progress bar
│   │   │   │   │   └── FetchProgress.module.css
│   │   │   │   └── hooks/
│   │   │   │       ├── useCommunications.ts   # Query + fetch hooks
│   │   │   │       └── useDiscovery.ts        # Vehicle discovery hooks
│   │   │   ├── vehicles/
│   │   │   │   ├── components/
│   │   │   │   │   ├── AddVehicleModal.tsx     # Add vehicle form
│   │   │   │   │   ├── AddVehicleModal.module.css
│   │   │   │   │   ├── VehicleCard.tsx         # Vehicle card
│   │   │   │   │   ├── VehicleCard.module.css
│   │   │   │   │   ├── VehicleGrid.tsx         # Vehicle grid overview
│   │   │   │   │   └── VehicleGrid.module.css
│   │   │   │   └── hooks/
│   │   │   │       └── useVehicles.ts          # Vehicle CRUD hooks
│   │   │   └── queryKeys.ts    # React Query key factory
│   │   ├── assets/             # Static assets
│   │   ├── App.tsx             # Root component — slim shell (90 lines)
│   │   ├── main.tsx            # React DOM entry point
│   │   └── index.css           # Global styles
│   ├── dist/                   # Built output
│   ├── public/                 # Static files
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts          # Vite config + API proxy
│   ├── tsconfig.json
│   ├── tsconfig.app.json
│   ├── tsconfig.node.json
│   └── eslint.config.js
├── .cache/                     # Pickle cache files (legacy)
├── __pycache__/                # Legacy Python cache
├── *.py                        # Legacy root-level Python scripts (pre-refactor)
├── .planning/                  # GSD planning documents
├── AGENTS.md                   # Agent instructions
├── README.md
└── .gitignore
```

## Directory Purposes

**`backend/src/`:**
- Purpose: All backend application code
- Contains: FastAPI app, feature modules, database, config
- Key files: `main.py` (app entry), `database.py` (MongoDB), `config.py` (settings)

**`backend/src/communications/`:**
- Purpose: Communications feature (NHTSA data fetching, storage, querying)
- Contains: Router, service, schemas, NHTSA API client
- Key files: `service.py` (378 lines, core business logic), `nhtsa_client.py` (263 lines, external API)

**`backend/src/vehicles/`:**
- Purpose: Vehicle tracking feature (CRUD for monitored vehicles)
- Contains: Router, service, schemas
- Key files: `schemas.py` (defines `CamelModel` base used project-wide)

**`backend/src/scripts/`:**
- Purpose: One-off data scripts
- Contains: Vehicle catalog seeder
- Key files: `seed_vehicle_catalog.py` (103 lines)

**`frontend/src/client/`:**
- Purpose: Typed API client layer (HTTP abstraction over backend)
- Contains: Base HTTP utilities, per-feature API modules, shared TypeScript types
- Key files: `types.ts` (206 lines, all shared interfaces), `api.ts` (base fetch wrapper)

**`frontend/src/features/`:**
- Purpose: Feature-organized frontend code (components + hooks per domain)
- Contains: `communications/` and `vehicles/` feature modules
- Key files: `queryKeys.ts` (shared query key factory)

**`frontend/src/contexts/`:**
- Purpose: React context providers for global state
- Contains: AppContext (selectedVehicleId, showAddModal via useReducer)
- Key files: `AppContext.tsx`

**`frontend/src/components/layout/`:**
- Purpose: Layout shell and navigation components
- Contains: AppShell (two-column layout), Sidebar, SidebarVehicleItem, StatsBar
- Key files: `AppShell.tsx` (react-resizable-panels), `Sidebar.tsx`

**`frontend/src/components/`:**
- Purpose: Shared/cross-feature UI components
- Contains: Header, FilterInfoModal, layout/
- Key files: `Header.tsx`, `FilterInfoModal.tsx`

## Key File Locations

**Entry Points:**
- `backend/src/main.py`: FastAPI application factory, router mounting, CORS, lifespan
- `frontend/src/main.tsx`: React DOM render entry
- `frontend/src/App.tsx`: Root component, QueryClient provider, Dashboard logic

**Configuration:**
- `backend/src/config.py`: Pydantic Settings (MongoDB URL, API host/port, CORS origins)
- `backend/.env`: Environment variable overrides (exists, not committed)
- `backend/pyproject.toml`: Python dependencies, linting config
- `frontend/vite.config.ts`: Vite config, path aliases (`@` -> `src/`), API proxy
- `frontend/tsconfig.json`: TypeScript config
- `frontend/package.json`: Node dependencies

**Core Logic:**
- `backend/src/communications/service.py`: Communication fetch-and-store pipeline, filtering, stats
- `backend/src/communications/nhtsa_client.py`: All NHTSA API interactions
- `backend/src/communications/schemas.py`: Type detection logic + Pydantic response models
- `backend/src/vehicles/service.py`: Vehicle CRUD operations against MongoDB

**API Client:**
- `frontend/src/client/api.ts`: Base `request()` function, `ApiError` class
- `frontend/src/client/communications.ts`: Communication API (list, get, fetch SSE, discovery)
- `frontend/src/client/vehicles.ts`: Vehicle API (list, get, create, update, delete)

**React Query:**
- `frontend/src/features/queryKeys.ts`: Cache key factory for vehicles + communications
- `frontend/src/features/vehicles/hooks/useVehicles.ts`: Vehicle query/mutation hooks
- `frontend/src/features/communications/hooks/useCommunications.ts`: Communication query + SSE fetch hook
- `frontend/src/features/communications/hooks/useDiscovery.ts`: Vehicle discovery cascading query hooks

## Naming Conventions

**Files:**
- Backend: `snake_case.py` (e.g., `nhtsa_client.py`, `seed_vehicle_catalog.py`)
- Frontend components: `PascalCase.tsx` (e.g., `VehicleCard.tsx`, `CommunicationList.tsx`)
- Frontend hooks: `camelCase.ts` prefixed with `use` (e.g., `useVehicles.ts`)
- Frontend client: `camelCase.ts` (e.g., `api.ts`, `vehicles.ts`)

**Directories:**
- Backend: `snake_case` (e.g., `communications/`, `vehicles/`)
- Frontend: `camelCase` or `lowercase` (e.g., `features/`, `client/`, `components/`)

**Backend module structure:**
- Each feature has: `__init__.py`, `router.py`, `service.py`, `schemas.py`
- Optional: additional files like `nhtsa_client.py` for external integrations

## Where to Add New Code

**New Backend Feature (e.g., "recalls"):**
- Create `backend/src/recalls/` with `__init__.py`, `router.py`, `service.py`, `schemas.py`
- Register router in `backend/src/main.py`: `app.include_router(recalls_router, prefix="/api")`
- Schemas should inherit from `CamelModel` (imported from `backend/src/vehicles/schemas.py`)

**New Backend Endpoint on Existing Feature:**
- Add route handler to `backend/src/{feature}/router.py`
- Add business logic to `backend/src/{feature}/service.py`
- Add request/response schemas to `backend/src/{feature}/schemas.py`

**New Frontend Feature:**
- Create `frontend/src/features/{name}/components/` and `frontend/src/features/{name}/hooks/`
- Add API functions to `frontend/src/client/{name}.ts`
- Add TypeScript interfaces to `frontend/src/client/types.ts`
- Add query keys to `frontend/src/features/queryKeys.ts`
- Export from `frontend/src/client/index.ts`

**New Frontend Component (shared):**
- Place in `frontend/src/components/`

**New Frontend Component (feature-specific):**
- Place in `frontend/src/features/{feature}/components/`

**New React Query Hook:**
- Place in `frontend/src/features/{feature}/hooks/`
- Use query key factory from `frontend/src/features/queryKeys.ts`

**New External API Integration:**
- Create dedicated client class in the relevant feature module (see `backend/src/communications/nhtsa_client.py` as pattern)

**Utilities:**
- Backend: No shared utils directory exists. Place in relevant feature module or create `backend/src/utils/`
- Frontend: Use `frontend/src/client/` for API-related utilities

## Special Directories

**`backend/venv/`:**
- Purpose: Python virtual environment
- Generated: Yes
- Committed: No

**`frontend/dist/`:**
- Purpose: Vite production build output
- Generated: Yes
- Committed: Yes (currently committed but should not be)

**`frontend/node_modules/`:**
- Purpose: Node.js dependencies
- Generated: Yes
- Committed: No

**`.cache/`:**
- Purpose: Legacy pickle cache files from pre-refactor CLI scripts
- Generated: Yes
- Committed: Likely yes (should not be)

**Root-level `*.py` files (`main.py`, `comms.py`, `config.py`, `processing.py`, `http_utils.py`, `cache_utils.py`):**
- Purpose: Legacy CLI-based Python scripts from before the full-stack refactor
- Generated: No
- Committed: Yes
- Note: These are the original pre-FastAPI implementation. Not used by the current backend.

---

*Structure analysis: 2026-03-27*
