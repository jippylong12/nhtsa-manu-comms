# Database (Railway Postgres + pgvector)

Everything the app stores lives in one Postgres database with `pgvector`, hosted
on Railway. Only the database is hosted; the sync and processor jobs run locally
on Marcus's machine.

There was briefly a second store. Phase A introduced this one as the canonical
home for the processed corpus but left MongoDB backing the vehicles and
communications endpoints, so the two disagreed in public: a vehicle card could
read "389 communications" above a detail page reading none. The project "NHTSA
Comms - One Postgres, One-Click Refresh" removed MongoDB entirely. Nothing in
the codebase talks to it, `motor` is no longer a dependency, and
`tests/test_no_mongo.py` makes the driver unimportable so a third store cannot
appear by accident.

## Current instance

| Property | Value |
|----------|-------|
| Railway project | `nhtsa-comms` (workspace `JippylongTwelve`) |
| Service | `pgvector` |
| Image | `pgvector/pgvector:pg18` |
| Postgres | 18.4 |
| pgvector | 0.8.5 |

The connection string is exposed to local workers as `DATABASE_URL` in
`backend/.env`, which is gitignored. Never commit it and never paste it into
Linear, issues, or chat.

## Re-provisioning from scratch

```bash
# 1. Authenticate (once per machine)
railway login

# 2. Create the project
railway init --name nhtsa-comms --workspace JippylongTwelve

# 3. Deploy the pgvector template (NOT plain postgres, which lacks the extension)
railway deploy -t postgres-with-pgvector-engine

# 4. Link the service so `railway variables` resolves
railway service pgvector

# 5. Write the public connection string into backend/.env without echoing it
cd backend
railway variables --service pgvector --json | python3 -c "
import sys, json, pathlib
url = json.load(sys.stdin)['DATABASE_URL']
p = pathlib.Path('.env')
lines = [l for l in p.read_text().splitlines() if not l.startswith('DATABASE_URL=')]
lines.append('DATABASE_URL=' + url)
p.write_text('\n'.join(lines) + '\n')
"
```

Railway's `DATABASE_URL` already points at the public TCP proxy
(`*.proxy.rlwy.net`), so it works from a local machine as-is.
`DATABASE_URL_PRIVATE` is the in-network address and will not resolve locally.

## Verifying the instance

```bash
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"   # psql from the libpq keg
cd backend
DBURL=$(python3 -c "from dotenv import dotenv_values; print(dotenv_values('.env')['DATABASE_URL'])")

psql "$DBURL" -c "SELECT version();"
psql "$DBURL" -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql "$DBURL" -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
```

Vector round-trip smoke test:

```sql
CREATE TEMP TABLE _vt(id int, e vector(3));
INSERT INTO _vt VALUES (1,'[1,2,3]'), (2,'[4,5,6]');
SELECT id, e, ROUND((e <=> '[1,2,3]')::numeric, 6) AS cosine_dist
FROM _vt ORDER BY e <=> '[1,2,3]';
```

Expected: row 1 at distance `0.000000`, row 2 at `0.025368`.

## The test database (local)

The suite does **not** run against Railway. It runs against a local replica,
selected by `TEST_DATABASE_URL` in `backend/.env`; unset, it falls back to
`DATABASE_URL` and the problem below comes back.

```bash
cd backend
bash tools/setup_test_db.sh          # build it
bash tools/setup_test_db.sh --reseed # refresh the corpus from Railway
python3 -m pytest -q                 # ~10s, 199 passed
```

### Why

pytest-asyncio gives every test its own event loop, and an asyncpg pool cannot
be shared across loops, so every test opens its own connection (see the
`_reset_pool` fixture in `tests/conftest.py`). That was tolerable at ~100 tests.
At ~200 it is not: Railway's public TCP proxy (`*.proxy.rlwy.net`) throttles
the churn, and past roughly half the suite tests start failing at fixture setup
with `TimeoutError` out of `asyncpg.connect()` — never on an assertion. Smaller
groups always passed, which is what made it look intermittent.

Postgres itself was never the constraint: `max_connections` is 100, and
`pg_stat_activity` showed 9 connections in use during a failing run. The limit
is the proxy, and it is not tunable from this side.

Locally there is no proxy and no round trip, so the same 199 tests run in about
ten seconds instead of eight to seventeen minutes, and they stop mutating the
production database as a side effect of running.

### Why it is a replica, not an empty schema

Three tests — two in `test_symptom_retrieval.py`, one in `test_usage_ledger.py`
— assert against the real corpus by `nhtsa_id` (for example `11006511`/PI0754G,
linked solely to vehicle 218944). They are only meaningful against real data, so
setup restores a `pg_dump` of Railway rather than starting empty. The dump is a
single connection, so it costs nothing in churn.

The local instance matches Railway exactly — Postgres 18.4, pgvector 0.8.5 —
because the suite depends on generated `tsvector` columns, `hnsw` indexes, and
real constraint behaviour. It listens on **5433** so an existing local Postgres
keeps 5432. `conftest.py` applies any pending migrations at import, so a new
migration cannot silently leave the test database behind.

The corpus dump carries the document embeddings, which is what lets the symptom
searches in `test_usage_ledger.py` run offline: the *query* vector is replayed
from `tests/fixtures/query_embeddings.json` and the *document* vectors it is
compared against come from the dump. See "What the test suite spends" in
docs/cost-ledger.md — a separate, pre-existing problem from the connection
churn above.

## Billing

The database runs on the Railway Hobby plan and draws from the monthly credit
allowance. Usage and current spend are visible in the Railway dashboard under
the `nhtsa-comms` project (`railway open`). A single small Postgres instance
with a modest volume is the only billable resource this phase creates.

## Connecting interactively

```bash
railway connect pgvector   # opens psql against the service
```
