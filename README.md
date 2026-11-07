# NHTSA Manufacturer Communications Tracker

A full-stack web application for fetching, filtering, and viewing NHTSA manufacturer communications for specific vehicles.

## Tech Stack

### Backend
- **FastAPI** - Modern async Python web framework
- **Postgres** + **asyncpg** + **pgvector** - the single store, including embeddings
- **Pydantic V2** - Data validation and settings management
- **httpx** - Async HTTP client for NHTSA API

### Frontend
- **React 19** + **TypeScript** - UI framework
- **Vite** - Build tool and dev server
- **TanStack Query v5** - Server state management
- **Lucide React** - Icon library

## Prerequisites

- **Python 3.11+**
- **Node.js 18+**
- **Postgres** with the **pgvector** extension, reachable at `DATABASE_URL`.
  Hosted (Railway and similar) or local both work; see `docs/database.md`.

## Quick Start

### 1. Apply migrations

```bash
cd backend && python -m src.db.migrate
```

Plain ordered SQL files under `backend/migrations/`, each applied once and
recorded. Re-running is a no-op. `--status` shows what is pending without
applying anything.

### 2. Start Backend

```bash
scripts/dev-api.sh            # 127.0.0.1:8000, runs from any directory
scripts/dev-api.sh --reload   # extra arguments pass through to uvicorn
```

Backend API will be available at `http://localhost:8000`.
API docs at `http://localhost:8000/docs`.

> **Use the script, not a bare `uvicorn src.main:app`.** `src` is a generic
> top-level package name, and an editable install of another project can put its
> own `src` on `sys.path` for every interpreter on the machine. Start uvicorn
> from anywhere except `backend/` and it will quietly serve *that* project
> instead: the server boots cleanly, logs a normal startup, and then 404s every
> route, which looks exactly like the database has been wiped. The script pins
> the import with `--app-dir` and refuses to start if anything still shadows it.

First-time setup, if you want an isolated environment:

```bash
cd backend
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -e .
```

### 3. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend will be available at `http://localhost:5175`.

## Features

### Vehicle Tracking
- Add vehicles by NHTSA Vehicle ID
- Configure model year, name, and filter keywords
- View communication count and last fetch time

### Communication Types
Communications are categorized and color-coded by type:
- **TSB** (Orange) - Technical Service Bulletins
- **PIT** (Blue) - Preliminary Information Technical
- **PIC** (Teal) - Preliminary Information Customer
- **PIP** (Purple) - Preliminary Information Parts
- **OTHER** (Gray) - Unknown/Other types

### Search & Filter
- Full-text search across LLM summaries, symptoms, remedies and full document text
- Filter by communication type, by affected system, and by whether a document exists
- Semantic symptom search over embeddings

### Refresh
Press refresh on a vehicle and the whole pipeline runs for it: fetch from NHTSA,
download and read the documents, analyse them, index them for search.

- A free preview first, showing how many bulletins are new, roughly what it will
  cost and roughly how long it will take. Nothing is billed until you confirm.
- Per-stage progress, because the analysis stage is queued with the model
  provider and can be quiet for minutes.
- Progress lives server-side, so leaving the page and coming back picks the run
  up where it is.
- One run at a time, and the actual metered cost is reported at the end.

## API Endpoints

### Vehicles
- `POST /api/vehicles` - Add a vehicle to track
- `GET /api/vehicles` - List all tracked vehicles
- `GET /api/vehicles/{id}` - Get vehicle by NHTSA ID
- `PATCH /api/vehicles/{id}` - Update vehicle config
- `DELETE /api/vehicles/{id}` - Remove vehicle

### Communications (corpus)
- `GET /api/corpus/communications` - List with filters (vehicle, type, status, systems, components, search)
- `GET /api/corpus/communications/{nhtsaId}` - One communication with its documents
- `GET /api/corpus/tags` - System and component vocabulary, optionally scoped to a vehicle

### Refresh
- `GET /api/refresh/{vehicleId}/preview` - What a run would do and cost. Free.
- `POST /api/refresh/{vehicleId}` - Start the run. Billed. Returns 202.
- `GET /api/refresh/{vehicleId}` - Progress of the current or most recent run

### Discovery (fills the add-vehicle form)
- `GET /api/communications/discovery/years|makes|models` - Served from the flat-file index; no NHTSA call

### Other
- `GET /api/symptom-search` - Semantic search with a composed answer
- `GET /api/admin/usage` - Cost ledger

## Environment Variables

Create `.env` in the `backend/` directory:

```env
DATABASE_URL=postgresql://user:password@host:port/database
API_HOST=0.0.0.0
API_PORT=8000
NHTSA_API_BASE_URL=https://api.nhtsa.gov

# Required by the analysis stage of a refresh
GEMINI_API_KEY=...
```

## Architecture

This project follows the architecture defined in `AGENTS.md`:

```
project_root/
├── backend/                 # FastAPI (Service Layer Pattern)
│   ├── src/
│   │   ├── main.py          # Entry point
│   │   ├── db/              # Postgres pool and migration runner
│   │   ├── vehicles/        # Vehicles feature module
│   │   └── communications/  # Communications feature module
│   └── pyproject.toml
├── frontend/                # React + Vite
│   ├── src/
│   │   ├── client/          # API client & types
│   │   ├── features/        # Feature modules
│   │   └── components/      # Shared components
│   └── package.json
└── README.md
```

## License

No license specified. Add one if you intend to distribute or open-source.
