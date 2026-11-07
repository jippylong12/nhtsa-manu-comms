# Deploying to Railway

One Docker image (root `Dockerfile`) holds the API and the built frontend. FastAPI serves the SPA
and `/api/*` from one domain on port 8000. Two Railway services use that image.

## Services

| Service | Start command | Notes |
|---------|---------------|-------|
| `api` | image CMD, `backend/start.sh` (runs `python -m src.db.migrate`, then `exec uvicorn`) | `railway.json` sets the Dockerfile builder, healthcheck `/api/health`, restart on failure |
| `nightly` | `python -m src.jobs.run_pipeline --send-digest` | same image, cron schedule `0 9 * * *` (UTC), does not migrate |

Railway cron runs the start command to completion and expects the process to exit; a new run is
skipped while the previous one is still running. Set the cron schedule and start command in the
`nightly` service settings (service-level overrides of `railway.json`).

## Early read-only deploy

Set `READ_ONLY=true` on `api`. Vehicles can be listed and read, the corpus and symptom search work,
but the add, edit and remove vehicle routes, the refresh routes and `/api/admin/usage` are not
mounted (404), and `GET /api/health` reports `"readOnly": true`. The frontend reads that flag and
hides Refresh, Add Vehicle and the remove control. When auth lands (M1) the flag goes back to false.

## Frontend build variables

`VITE_FIREBASE_*` values are build-time. They are `ARG`s in Dockerfile stage 1; create matching
Railway variables on the `api` service and Railway passes them to the build.

## Environment matrix

"api" and "nightly" are the two services. Secret = never commit, set in Railway only.

| Variable | api | nightly | Secret | Purpose |
|----------|-----|---------|--------|---------|
| `DATABASE_URL` | yes | yes | yes | Postgres connection string |
| `POSTGRES_POOL_MAX` | optional | optional | no | Pool size (default 10) |
| `TEST_DATABASE_URL` | no | no | yes | Local test database; tests only |
| `CLAUDE_API_KEY` | no | yes | yes | Anthropic key for extraction (`ANTHROPIC_API_KEY` is accepted too) |
| `ANTHROPIC_API_KEY` | no | yes | yes | Alias of `CLAUDE_API_KEY` |
| `ANTHROPIC_TIMEOUT_SECONDS` | no | optional | no | Per-call ceiling (default 300) |
| `ANTHROPIC_EXTRACTION_CONCURRENCY` | no | optional | no | Live extraction calls at once (default 6) |
| `EXTRACTION_MODEL` | no | optional | no | Extraction model (default `claude-haiku-5-5`) |
| `GEMINI_API_KEY` | yes | yes | yes | Symptom search and embeddings |
| `GEMINI_MODEL` | optional | optional | no | Compose model |
| `GEMINI_EMBEDDING_MODEL` | optional | optional | no | Embedding model |
| `GEMINI_TIMEOUT_SECONDS` | optional | optional | no | Per-call ceiling (default 120) |
| `EMBEDDING_DIMENSIONS` | optional | optional | no | Vector size (default 1536) |
| `RESEND_API_KEY` | no | yes | yes | Sends the digest |
| `DIGEST_FROM_EMAIL` | no | yes | no | Digest sender |
| `DIGEST_TO_EMAIL` | no | yes | no | Digest recipient |
| `APP_BASE_URL` | no | yes | no | Links in the digest and emails (planned, M1) |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | yes | no | yes | Verifies Firebase ID tokens (planned, M1) |
| `ADMIN_FIREBASE_UIDS` | yes | no | no | Admin accounts (planned, M1) |
| `STRIPE_SECRET_KEY` | yes | no | yes | Stripe API (planned, M1) |
| `STRIPE_WEBHOOK_SECRET` | yes | no | yes | Stripe webhook check (planned, M1) |
| `STRIPE_PRICE_UNLOCK` | yes | no | no | Stripe price id (planned, M1) |
| `STRIPE_PRICE_MONTHLY` | yes | no | no | Stripe price id (planned, M1) |
| `CORS_ORIGINS` | optional | no | no | JSON list; the SPA is same-origin in production, leave the default |
| `READ_ONLY` | yes (`true` for the early deploy) | no | no | Read-only mode, see above |
| `FRONTEND_DIST` | optional | no | no | Built frontend directory (default `../frontend/dist`; the image sets `/app/frontend/dist`) |
| `PDF_ARCHIVE_DIR` | no | yes | no | Where PDFs are archived: a Railway volume mount or `/tmp` |
| `PDF_DOWNLOAD_CONCURRENCY` | no | optional | no | Parallel PDF downloads (default 3) |
| `LOW_YIELD_CHARS_PER_PAGE` | no | optional | no | Vision-fallback threshold (default 200) |
| `NHTSA_API_BASE_URL` | optional | optional | no | NHTSA API base |
| `API_HOST` | optional | no | no | Bind host (default 0.0.0.0) |
| `API_PORT` | optional | no | no | Settings default 8000; the image CMD binds 8000 |
| `FLATFILE_CACHE_DIR` | optional | optional | no | Flat-file zip cache directory |
| `VITE_FIREBASE_*` | build | no | no | Frontend build variables (see above) |

## Local proof

```
cd frontend && npm run build
cd ../backend && FRONTEND_DIST=../frontend/dist uvicorn src.main:app --port 8765
curl -s localhost:8765/ | head -c 200
curl -s localhost:8765/api/health
docker build -t nhtsa-comms .
```
