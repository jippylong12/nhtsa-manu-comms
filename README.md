# NHTSA Manufacturer Communications Tracker

A full-stack web application for fetching, filtering, and viewing NHTSA manufacturer communications for specific vehicles.

## Tech Stack

### Backend
- **FastAPI** - Modern async Python web framework
- **MongoDB** + **Motor** - NoSQL database with async driver
- **Pydantic V2** - Data validation and settings management
- **httpx** - Async HTTP client for NHTSA API

### Frontend
- **React 19** + **TypeScript** - UI framework
- **Vite** - Build tool and dev server
- **TanStack Query v5** - Server state management
- **Lucide React** - Icon library

## Architecture

This project follows the architecture defined in `AGENTS.md`:

```
project_root/
├── backend/                 # FastAPI (Service Layer Pattern)
│   ├── src/
│   │   ├── main.py          # Entry point
│   │   ├── database.py      # MongoDB connection
│   │   ├── vehicles/        # Vehicles feature module
│   │   └── communications/  # Communications feature module
│   └── pyproject.toml
├── frontend/                # React + Vite
│   ├── src/
│   │   ├── client/          # API client & types
│   │   ├── features/        # Feature modules
│   │   └── components/      # Shared components
│   └── package.json
└── docker-compose.yml       # MongoDB infrastructure
```

## Quick Start

### 1. Start MongoDB

```bash
docker-compose up -d
```

MongoDB will be available at `localhost:27017`.
Mongo Express GUI (optional) at `http://localhost:8081`.

### 2. Start Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -e .
uvicorn src.main:app --reload --port 8000
```

Backend API will be available at `http://localhost:8000`.
API docs at `http://localhost:8000/docs`.

### 3. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend will be available at `http://localhost:5173`.

## Features

### Vehicle Tracking
- Add vehicles by NHTSA Vehicle ID
- Configure model year, name, and filter keywords
- View communication count and last fetch time

### Communication Fetching
- Real-time SSE progress updates during fetch
- Caches communications in MongoDB (no duplicate requests)
- Force refresh option to update cached data

### Communication Viewing
- Expandable list with full details
- Associated documents with download links
- Keyword highlighting for matched summary text
- Filter by vehicle, year, model, keywords

## API Endpoints

### Vehicles
- `POST /api/vehicles` - Add a vehicle to track
- `GET /api/vehicles` - List all tracked vehicles
- `GET /api/vehicles/{id}` - Get vehicle by NHTSA ID
- `PATCH /api/vehicles/{id}` - Update vehicle config
- `DELETE /api/vehicles/{id}` - Remove vehicle

### Communications
- `GET /api/communications` - List with filters
- `GET /api/communications/{id}` - Get by NHTSA ID
- `POST /api/communications/fetch` - SSE streaming fetch
- `POST /api/communications/fetch-sync` - Synchronous fetch

## Environment Variables

Create `.env` in the `backend/` directory:

```env
MONGODB_URL=mongodb://localhost:27017
MONGODB_DATABASE=nhtsa_comms
API_HOST=0.0.0.0
API_PORT=8000
NHTSA_API_BASE_URL=https://api.nhtsa.gov
```

## License

No license specified. Add one if you intend to distribute or open-source.
