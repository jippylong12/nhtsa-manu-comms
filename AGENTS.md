# AGENTS.md - Full Stack (FastAPI + React) Configuration

## 1. Project Architecture (Monorepo)
**Philosophy:** Backend is the Source of Truth. Frontend is a consumer.
**Critical Rule:** The Backend defines the Contract (OpenAPI). The Frontend generates code from that contract.

### Directory Structure
```text
project_root/
├── backend/                 # FastAPI (Service Layer Pattern)
│   ├── pyproject.toml
│   ├── alembic.ini
│   └── src/
│       ├── main.py          # Entry point
│       ├── auth/            # Feature Module
│       │   ├── router.py    # Endpoints
│   │   │   ├── service.py   # Business Logic
│   │   │   └── schemas.py   # Pydantic Models
│       └── database.py
├── frontend/                # React + Vite (Feature-Sliced Lite)
│   ├── package.json
│   ├── openapi-config.ts    # CodeGen Config
│   └── src/
│       ├── client/          # AUTO-GENERATED API Client
│       ├── components/
│       └── features/        # React Features
│           └── auth/
│               ├── components/
│               └── hooks/   # Uses generated client
├── docker-compose.yml       # Dev Infrastructure (DB, Redis)
└── README.md
```

## 2. Backend Standards (Python/FastAPI)
* **Framework:** FastAPI.
* **Architecture:** Router -> Service -> DB.
    * **Routers** (`router.py`) must be thin. No DB queries allowed.
    * **Services** (`service.py`) handle all business logic.
* **Schema:** strict **Pydantic V2** models.
    * Input models (`UserCreate`) must be separate from Output models (`UserResponse`).
    * **Snake_case** in Python, but configured to output **camelCase** JSON for Javascript compatibility (using Pydantic `alias_generator`).
* **Docs:** Meaningful `summary` and `response_description` in route decorators are mandatory (this populates the frontend types).

## 3. Frontend Standards (React/TS)
* **Build:** Vite.
* **State:** TanStack Query (v5).
* **Fetching Strategy:**
    * **DO NOT** write manual `fetch` or `axios` calls.
    * **DO** use an OpenAPI Generator (e.g., `hey-api` or `openapi-typescript-codegen`) to generate a typed client from the FastAPI `http://localhost:8000/openapi.json`.
* **Hooks:**
    * Wrap generated API calls in custom Query Hooks.
    * *Example:* `useUserQuery` wraps `ApiClient.services.users.getMe()`.

## 4. Integration Strategy (The "Expert" Way)
**The Contract:** The `openapi.json` is the bridge.

1.  **Dev Workflow:**
    * Backend changes Pydantic model -> Server auto-reloads.
    * Frontend runs `npm run gen-client` -> Updates TS interfaces in `src/client/`.
    * TypeScript errors immediately flag breaking changes in the UI.

2.  **CORS & Proxy:**
    * In Dev: Use Vite `server.proxy` to forward `/api` requests to `localhost:8000`.
    * In Prod: Nginx or Docker handling.

## 5. Coding Standards

### Backend (Python)
* **Async:** All routes and DB calls must be `async`.
* **Typing:** Return types on Routers are mandatory (`-> UserResponse`). This ensures the OpenAPI spec is accurate.

### Frontend (React)
* **Query Keys:** Strict management using a `queryKeys.ts` factory to avoid cache invalidation bugs.
    * *Bad:* `['users', id]` (Magic strings)
    * *Good:* `userKeys.detail(id)`
* **Form Handling:** React Hook Form + Zod.
    * Note: You can often infer Zod schemas directly from the Pydantic-generated types if using advanced tooling, otherwise keep Zod manual but aligned.

## 6. Agent Instructions
1.  **Feature Additions:**
    * **Step 1 (Backend):** Create the Pydantic Schema and Endpoint.
    * **Step 2 (Shared):** Mention "I am running the client generator" (simulated).
    * **Step 3 (Frontend):** Build the UI using the *assumed* generated types from Step 1.
2.  **Refactoring:**
    * If you see manual `fetch('/api/users')` in a component, **REFACTOR** it to use the generated client methods immediately.
    * If the Backend `router.py` contains SQL, move it to `service.py`.
3.  **Naming:**
    * Backend variable: `user_id` (Python standard).
    * API JSON output: `userId` (CamelCase standard).
    * Frontend variable: `userId` (JS standard).