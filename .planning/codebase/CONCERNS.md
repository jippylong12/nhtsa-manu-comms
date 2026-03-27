# Codebase Concerns

**Analysis Date:** 2026-03-27

## Security Concerns

**No Authentication or Authorization:**
- Risk: All API endpoints are fully open with zero auth. Any client can create/delete vehicles, trigger fetches, and read all data.
- Files: `backend/src/main.py`, `backend/src/vehicles/router.py`, `backend/src/communications/router.py`
- Current mitigation: None. The app appears intended for local/personal use only.
- Recommendations: If this ever faces the internet, add API key middleware or OAuth. For personal use, consider binding to `127.0.0.1` instead of `0.0.0.0` in `backend/src/config.py` (line 22).

**Unescaped Regex in MongoDB Queries:**
- Risk: User-supplied `model` and `search` query params are passed directly into MongoDB `$regex` operators without escaping. A crafted input could cause ReDoS or unexpected query behavior.
- Files: `backend/src/communications/service.py` (lines 76, 81-82, 274)
- Current mitigation: None.
- Recommendations: Escape regex special characters before building queries, or use `$text` indexes with `$search` instead of `$regex`.

**CORS Allows Credentials with Wildcard Methods:**
- Risk: `allow_methods=["*"]` and `allow_headers=["*"]` with `allow_credentials=True` is overly permissive.
- Files: `backend/src/main.py` (lines 35-41)
- Current mitigation: Origins are restricted to localhost in default config.
- Recommendations: Restrict to actual methods used (GET, POST, PATCH, DELETE) and specific headers.

**Pickle-Based Caching (Legacy CLI):**
- Risk: `pickle.load()` is inherently insecure if cache files are tampered with. Deserializing untrusted pickle data can execute arbitrary code.
- Files: `cache_utils.py` (line 32)
- Current mitigation: Cache is local filesystem only, user-controlled.
- Recommendations: Use JSON for cache serialization instead of pickle.

## Performance Concerns

**New httpx Client Per Request:**
- Problem: `NHTSAClient` creates a new `httpx.AsyncClient` inside every method call (`async with httpx.AsyncClient(...)`) instead of reusing a persistent connection pool.
- Files: `backend/src/communications/nhtsa_client.py` (lines 30, 47, 115, 135, 149, 163, 183)
- Cause: No shared client lifecycle. Each request opens and closes a TCP connection.
- Improvement path: Create a shared `httpx.AsyncClient` in the `NHTSAClient.__init__` and close it on app shutdown via the lifespan context. This also enables HTTP/2 and connection reuse.

**No Pagination on Communications Fetch:**
- Problem: `fetch_and_store` in `CommunicationService` fetches ALL communication IDs for a vehicle and processes them sequentially. For vehicles with hundreds of communications, this creates a long-running request.
- Files: `backend/src/communications/service.py` (lines 108-289)
- Current mitigation: SSE progress stream keeps the client informed.
- Improvement path: Consider background task queue (e.g., Celery or simple asyncio task) so the endpoint returns immediately.

**Frontend Loads All Communications at Once:**
- Problem: `perPage: 100` is hardcoded in the filters, meaning the frontend requests up to 100 communications in a single API call. No infinite scroll or pagination UI exists.
- Files: `frontend/src/App.tsx` (line 58)
- Improvement path: Add pagination controls or virtual scrolling for vehicles with many communications.

**Hardcoded Discovery Year:**
- Problem: `current_year = 2026` is hardcoded instead of computed dynamically.
- Files: `backend/src/communications/service.py` (line 349)
- Impact: Will silently become stale. Should use `datetime.now().year + 1` or similar.

## Technical Debt

| Area | Issue | Severity | Effort |
|------|-------|----------|--------|
| Legacy CLI | Root-level Python files (`main.py`, `comms.py`, `processing.py`, `config.py`, `cache_utils.py`, `http_utils.py`) are a superseded CLI tool duplicating logic now in `backend/src/` | Med | S |
| Duplicated extraction logic | `extract_nhtsa_ids_from_details` exists in both `comms.py` (line 15) and `backend/src/communications/nhtsa_client.py` (line 207) with slightly different filtering logic (CLI filters out PI-prefixed comms, backend does not) | Med | S |
| Dead code | `processing.py` line 21: `essential_whitespace = str.split` is assigned but never used anywhere | Low | S |
| Dead code | `comms.py` has `discover_manufacturer_comm_ids` (line 158) which is never called -- only the `_with_summaries` variant is used | Low | S |
| Inline CSS | `App.tsx` (658 lines) contains ~200 lines of inline `<style>` blocks. Same pattern in `CommunicationList.tsx`, `VehicleCard.tsx`, `AddVehicleModal.tsx`, `FilterInfoModal.tsx`, `Header.tsx` | Med | M |
| God component | `App.tsx` contains the entire Dashboard view with all state, handlers, and views. Should be split into `DashboardView` and `CommunicationsView` | Med | M |
| No input validation on vehicle_id | `vehicle_id` path params are plain `int` with no range validation. Negative or zero IDs pass through to NHTSA API | Low | S |
| `__pycache__` committed | Root `__pycache__/` directory exists with `.pyc` files. While `.gitignore` covers `__pycache__/`, the directory is present on disk | Low | S |
| No DB migration framework | `backend/src/migrations.py` is a manual one-off script. No versioned migration system for schema changes | Med | M |

## Missing Infrastructure

**Zero Test Coverage:**
- There are no test files anywhere in the project. No unit tests, no integration tests, no E2E tests.
- `pyproject.toml` lists `pytest` and `pytest-asyncio` as dev dependencies but no tests exist.
- Frontend has no test runner configured at all.
- Priority: High. The comm type detection logic in `backend/src/communications/schemas.py` (lines 70-152) is particularly complex and fragile without tests.

**No Logging Framework:**
- All logging is `print()` statements. No structured logging, no log levels, no log aggregation.
- Files: `backend/src/database.py` (lines 32, 39), `comms.py` (line 148)
- Recommendations: Use Python `logging` module with structured output. Add request logging middleware to FastAPI.

**No CI/CD Pipeline:**
- No GitHub Actions, no Dockerfile, no deployment configuration.
- Recommendations: Add at minimum a lint + type-check CI step.

**No Rate Limiting on API:**
- The NHTSA fetch endpoint can be triggered repeatedly with no throttle. Each trigger makes many external API calls.
- Files: `backend/src/communications/router.py` (lines 73-100, 103-140)
- Recommendations: Add per-vehicle fetch cooldown or global rate limiter.

**No Health Check for MongoDB:**
- The `/api/health` endpoint returns `{"status": "healthy"}` unconditionally without actually checking MongoDB connectivity.
- Files: `backend/src/main.py` (lines 48-51)

## Code Smells

**Broad Exception Catching:**
- Multiple bare `except Exception` blocks silently swallow errors and return `None` or empty lists, making debugging difficult.
- Files: `comms.py` (lines 75, 121-122), `backend/src/communications/nhtsa_client.py` (line 88), `processing.py` (line 81)
- Pattern: Should catch specific exceptions and log errors.

**Duplicated Data Extraction Pattern:**
- The pattern of `details["results"][0]["safetyIssues"]["manufacturerCommunications"]` wrapped in try/except appears 5+ times across `comms.py` and `backend/src/communications/nhtsa_client.py`.
- Recommendation: Extract into a single shared utility.

**`import time` Inside Function Body:**
- `backend/src/communications/router.py` (line 116) and `backend/src/communications/service.py` (line 114) import `time` inside function bodies rather than at module level.
- Low severity but inconsistent with the rest of the codebase.

**Implicit `None` Caching:**
- `comms.py` caches `None` for failed fetches in the safety DB (line 146). This means a transient failure permanently prevents re-fetch for that ID until the cache is manually cleared.
- Files: `comms.py` (lines 124-127, 145-147)

## Dependency Risks

**No Lock File for Backend:**
- `backend/pyproject.toml` specifies minimum versions only (e.g., `fastapi>=0.109.0`) with no lock file (`requirements.txt` or `uv.lock`). Builds are not reproducible.
- Recommendations: Add `uv.lock` or pin exact versions.

**MongoDB Driver (motor 3.x):**
- Using `motor>=3.3.0` which wraps `pymongo`. The installed version is `pymongo-4.15.5`. These are actively maintained; no immediate risk.

**Frontend Dependencies Are Current:**
- React 19.2, Vite 7.2, TanStack Query 5.90 -- all recent versions. No stale deps detected.

## Test Coverage Gaps

**Everything:**
- What's not tested: The entire codebase -- all backend services, routers, NHTSA client, schemas, type detection, frontend components, hooks, API client.
- Files: All files under `backend/src/` and `frontend/src/`
- Risk: The comm type detection pipeline (`get_comm_type` in `backend/src/communications/schemas.py`) uses cascading text matching with 10+ patterns. Any change could silently break classification.
- Priority: High

## Recommended Priorities

1. **Add tests for comm type detection** -- `backend/src/communications/schemas.py` `get_comm_type()` is the core business logic and has zero coverage. Start here.
2. **Sanitize regex inputs** -- `backend/src/communications/service.py` passes raw user input to `$regex`. Quick fix with high security value.
3. **Remove legacy CLI files** -- `main.py`, `comms.py`, `processing.py`, `config.py`, `cache_utils.py`, `http_utils.py` at project root are dead weight and confuse the project structure.
4. **Reuse httpx client** -- `backend/src/communications/nhtsa_client.py` creates a new client per request. Share a client instance for connection pooling.
5. **Add structured logging** -- Replace all `print()` calls with `logging` module across `backend/src/`.
6. **Fix hardcoded year** -- `backend/src/communications/service.py` line 349. Trivial fix, avoids silent staleness.
7. **Split `App.tsx`** -- Extract Dashboard and Communications views into separate components to improve maintainability.
8. **Add backend lock file** -- Pin dependency versions for reproducible builds.

---

*Concerns audit: 2026-03-27*
