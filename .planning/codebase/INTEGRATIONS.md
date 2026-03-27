# External Integrations

**Analysis Date:** 2026-03-27

## APIs & External Services

### NHTSA API (National Highway Traffic Safety Administration)

The sole external API. All endpoints are public (no auth required). Base URL configured via `nhtsa_api_base_url` setting (default: `https://api.nhtsa.gov`).

| Endpoint | Purpose | Used In |
|----------|---------|---------|
| `GET /vehicles/{vehicleId}/details` | Fetch vehicle safety issues including manufacturer communication IDs and summaries | `backend/src/communications/nhtsa_client.py` (`get_vehicle_details`), `config.py` (legacy CLI) |
| `GET /safetyIssues/byNhtsaId` | Fetch full communication details by NHTSA ID (documents, products, dates) | `backend/src/communications/nhtsa_client.py` (`get_safety_issue`), `comms.py` (legacy CLI) |
| `GET /SafetyRatings` | Discover available model years | `backend/src/communications/nhtsa_client.py` (`get_model_years`) |
| `GET /vehicles/makes` | Get makes for a model year | `backend/src/communications/nhtsa_client.py` (`get_makes_for_year`) |
| `GET /vehicles/models` | Get models for a year/make | `backend/src/communications/nhtsa_client.py` (`get_models_for_make_year`) |
| `GET /vehicles/trims` | Get trims for a year/make/model | `backend/src/communications/nhtsa_client.py` (`get_trims_for_model`) |
| `GET /vehicles/byYmmt` | Get vehicle variants with correct `vehicleId` for communications | `backend/src/communications/nhtsa_client.py` (`get_vehicle_variants`) |

**Rate Limiting:**
- NHTSA API returns 403 on rate limits
- Backend client implements exponential backoff (1s, 2s, 4s) with 3 retries (`backend/src/communications/nhtsa_client.py`)
- Concurrency limited to 3 simultaneous requests with 0.2s inter-request delay (`fetch_communications_batch`)
- Legacy CLI uses `requests` retry adapter with `backoff_factor=0.6` on 403/408/429/5xx (`http_utils.py`)

**User-Agent:**
- Backend: `nhtsa-manu-comms/2.0 (+https://nhtsa.gov)` (`backend/src/communications/nhtsa_client.py`)
- Legacy CLI: `nhtsa-manu-comms/1.0 (+https://nhtsa.gov)` (`http_utils.py`)
- Seed script uses `Mozilla/5.0` via curl to avoid WAF blocking (`backend/src/scripts/seed_vehicle_catalog.py`)

### NHTSA Client Pattern

The `NHTSAClient` class (`backend/src/communications/nhtsa_client.py`) creates a fresh `httpx.AsyncClient` per request (no persistent connection pool). Key methods:

```python
client = NHTSAClient()
details = await client.get_vehicle_details(vehicle_id)
comm = await client.get_safety_issue(nhtsa_id, max_retries=3)

# Batch fetch with concurrency control and progress streaming
async for nhtsa_id, comm_data in client.fetch_communications_batch(ids, max_concurrent=3):
    # Process each result as it arrives
```

## Database Connections

**MongoDB:**
- Driver: Motor (async) via `motor.motor_asyncio.AsyncIOMotorClient`
- Connection managed in `backend/src/database.py` as a module-level singleton (`Database` class)
- Connected/disconnected via FastAPI lifespan events in `backend/src/main.py`
- Access pattern: `get_database()` returns `AsyncIOMotorDatabase` instance
- No ORM -- direct collection operations with dict documents

**Collections:**

| Collection | Purpose | Key Fields | Indexes |
|------------|---------|------------|---------|
| `vehicles` | Tracked vehicle configurations | `vehicle_id`, `year`, `model`, `keywords` | `vehicle_id` (unique) |
| `communications` | Cached NHTSA communications | `nhtsa_id`, `vehicle_id`, `communication_type`, `summary` | `nhtsa_id` (unique), `vehicle_id`, `communication_date` |
| `searches` | Search history | `created_at` | `created_at` |

## Internal API (Backend to Frontend)

The FastAPI backend exposes a REST API consumed by the React frontend. Vite proxies `/api` to `localhost:8000` in development.

**Vehicles Router** (`backend/src/vehicles/router.py`, prefix: `/api/vehicles`):

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/vehicles` | Create/upsert a tracked vehicle |
| `GET` | `/vehicles` | List tracked vehicles (paginated) |
| `GET` | `/vehicles/{vehicle_id}` | Get single vehicle |
| `PATCH` | `/vehicles/{vehicle_id}` | Update vehicle config |
| `DELETE` | `/vehicles/{vehicle_id}` | Delete vehicle + its communications |

**Communications Router** (`backend/src/communications/router.py`, prefix: `/api/communications`):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/communications` | List communications with filters (paginated) |
| `GET` | `/communications/{nhtsa_id}` | Get single communication |
| `POST` | `/communications/fetch` | SSE stream: fetch from NHTSA with progress |
| `POST` | `/communications/fetch-sync` | Synchronous fetch (waits for completion) |
| `GET` | `/communications/stats/{vehicle_id}` | Aggregated stats by type |
| `GET` | `/communications/discovery/years` | Available model years |
| `GET` | `/communications/discovery/makes` | Available makes for a year |
| `GET` | `/communications/discovery/models` | Models for year/make |
| `GET` | `/communications/discovery/variants` | Vehicle variants with vehicleId |
| `GET` | `/communications/discovery/trims` | Available trims for year/make/model |

**SSE (Server-Sent Events):**
- Fetch endpoint returns `text/event-stream` response
- Progress events: `{"status": "fetching", "progress": 0-100, "message": "...", "total_ids": N, "fetched_ids": N, "new_count": N}`
- Terminal events: status `"complete"` or `"error"`
- Frontend consumes via EventSource in `frontend/src/features/communications/hooks/useCommunications.ts`

**JSON Serialization:**
- Backend uses camelCase JSON output via `CamelModel` base class (`backend/src/vehicles/schemas.py`)
- `to_camel()` converts snake_case fields to camelCase for API responses
- Frontend types mirror this in `frontend/src/client/types.ts`

## Third-Party SDKs

No third-party SDKs beyond the NHTSA API. The application is self-contained with:
- `httpx` for async HTTP calls to NHTSA
- `requests` for sync HTTP calls in legacy CLI
- `motor` for MongoDB operations

## Environment Variables

| Variable | Purpose | Default | Required |
|----------|---------|---------|----------|
| `MONGODB_URL` | MongoDB connection string | `mongodb://localhost:27017` | No (has default) |
| `MONGODB_DATABASE` | MongoDB database name | `nhtsa_comms` | No (has default) |
| `API_HOST` | FastAPI bind host | `0.0.0.0` | No |
| `API_PORT` | FastAPI bind port | `8000` | No |
| `NHTSA_API_BASE_URL` | NHTSA API base URL | `https://api.nhtsa.gov` | No |
| `CORS_ORIGINS` | Allowed CORS origins (list) | `["http://localhost:5173", "http://127.0.0.1:5173"]` | No |

**Configuration source:** `backend/src/config.py` via `pydantic-settings`

**Secrets:**
- No API keys or authentication tokens required (NHTSA API is public)
- No `.env` file detected in repository (gitignored or not yet created)
- All defaults are functional for local development

## Authentication & Identity

**Auth Provider:** None
- No authentication or authorization on the API
- All endpoints are publicly accessible
- No user accounts or sessions

## Monitoring & Observability

**Error Tracking:** None (no Sentry, Datadog, etc.)

**Logs:**
- `print()` statements for connection status and fetch progress (`backend/src/database.py`, `backend/src/communications/service.py`)
- No structured logging framework

## CI/CD & Deployment

**Hosting:** Not configured (no Dockerfile, no deployment config detected)

**CI Pipeline:** Not detected (no `.github/workflows/`, no `Makefile`)

## Webhooks & Callbacks

**Incoming:** None
**Outgoing:** None

---

*Integration audit: 2026-03-27*
