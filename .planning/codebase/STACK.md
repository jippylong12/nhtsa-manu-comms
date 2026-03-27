# Technology Stack

**Analysis Date:** 2026-03-27

## Languages

**Primary:**
- Python 3.10+ - Backend API and CLI tools
- TypeScript ~5.9.3 - Frontend SPA

**Secondary:**
- CSS (vanilla) - Inline styles in components + `frontend/src/index.css`, `frontend/src/App.css`

## Runtime

**Environment:**
- Python 3.11 (detected from `__pycache__` bytecode: `cpython-311.pyc`)
- Node.js (version unspecified, no `.nvmrc`)

**Package Managers:**
- pip with `pyproject.toml` (hatchling build backend) - `backend/pyproject.toml`
- npm with `package-lock.json` - `frontend/package-lock.json`

## Frameworks

**Core:**

| Package | Version | Purpose |
|---------|---------|---------|
| FastAPI | >=0.109.0 | Backend REST API framework |
| React | ^19.2.0 | Frontend UI library |
| Vite | ^7.2.4 | Frontend build tool and dev server |

**Testing:**

| Package | Version | Purpose |
|---------|---------|---------|
| pytest | >=7.4.0 | Python test runner (dev dependency) |
| pytest-asyncio | >=0.23.0 | Async test support for FastAPI (dev dependency) |

**Build/Dev:**

| Tool | Version | Purpose |
|------|---------|---------|
| hatchling | latest | Python build backend (`backend/pyproject.toml`) |
| uvicorn[standard] | >=0.27.0 | ASGI server for FastAPI |
| black | >=24.0.0 | Python code formatter (line-length: 100, target: py310) |
| ruff | >=0.1.0 | Python linter (rules: E, F, I, W) |
| ESLint | ^9.39.1 | TypeScript/React linter |
| TypeScript | ~5.9.3 | Type checking |

## Key Dependencies

**Backend Critical:**

| Package | Version | Purpose |
|---------|---------|---------|
| motor | >=3.3.0 | Async MongoDB driver (Motor wraps PyMongo for asyncio) |
| httpx | >=0.26.0 | Async HTTP client for calling NHTSA API |
| pydantic | >=2.5.0 | Request/response schema validation |
| pydantic-settings | >=2.1.0 | Environment-based configuration (`backend/src/config.py`) |
| python-dotenv | >=1.0.0 | `.env` file loading |
| bson (via pymongo) | transitive | MongoDB ObjectId handling |

**Frontend Critical:**

| Package | Version | Purpose |
|---------|---------|---------|
| @tanstack/react-query | ^5.90.16 | Server state management, caching, SSE handling |
| date-fns | ^4.1.0 | Date formatting utilities |
| lucide-react | ^0.562.0 | Icon library |
| @vitejs/plugin-react | ^5.1.1 | React JSX transform for Vite |

**Legacy CLI (root-level):**

| Package | Purpose |
|---------|---------|
| requests | Synchronous HTTP client for NHTSA API |
| pickle (stdlib) | Local file-based caching in `.cache/` directory |

## Configuration

**Environment:**
- Backend uses `pydantic-settings` with `.env` file support (`backend/src/config.py`)
- Settings loaded via `get_settings()` with `@lru_cache` singleton pattern
- Key settings: `mongodb_url`, `mongodb_database`, `nhtsa_api_base_url`, `cors_origins`, `api_host`, `api_port`
- Defaults are development-friendly (localhost MongoDB, port 8000, Vite CORS origin)

**Build:**
- `backend/pyproject.toml` - Python project metadata, build config, tool settings
- `frontend/vite.config.ts` - Vite config with React plugin, `@` path alias to `./src`, API proxy to `:8000`
- `frontend/tsconfig.json` - References `tsconfig.app.json` and `tsconfig.node.json`
- `frontend/eslint.config.js` - ESLint flat config with react-hooks and react-refresh plugins

**Frontend Dev Server:**
- Port 5173 (Vite default)
- Proxies `/api` requests to `http://localhost:8000` (`frontend/vite.config.ts`)

**Path Aliases:**
- Frontend: `@` maps to `frontend/src/` (`frontend/vite.config.ts`)

## Infrastructure

**Database:**
- MongoDB (default: `mongodb://localhost:27017`, database: `nhtsa_comms`)
- Async driver: Motor (`motor.motor_asyncio`)
- Collections: `vehicles`, `communications`, `searches`
- Indexes created at startup in `backend/src/database.py`:
  - `vehicles.vehicle_id` (unique)
  - `communications.nhtsa_id` (unique)
  - `communications.vehicle_id`
  - `communications.communication_date`
  - `searches.created_at`

**Caching:**
- Backend: MongoDB acts as the communication cache (upsert pattern avoids re-fetching from NHTSA)
- Frontend: TanStack Query with 5-minute stale time (`frontend/src/App.tsx`)
- Legacy CLI: Pickle files in `.cache/` directory with daily rotation (`cache_utils.py`)

**File Storage:**
- Local filesystem only (no cloud storage)

## Platform Requirements

**Development:**
- Python 3.10+
- Node.js (recent LTS)
- MongoDB instance (local or remote)
- No Docker configuration detected

**Production:**
- MongoDB instance
- uvicorn ASGI server
- Static frontend build served separately or via reverse proxy

---

*Stack analysis: 2026-03-27*
