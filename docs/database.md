# Canonical Database (Railway Postgres + pgvector)

Phase A stores the processed communications corpus in a Postgres database with
`pgvector`, hosted on Railway. Only the database lives on Railway in this phase;
the sync and processor jobs run locally on Marcus's machine.

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

## Billing

The database runs on the Railway Hobby plan and draws from the monthly credit
allowance. Usage and current spend are visible in the Railway dashboard under
the `nhtsa-comms` project (`railway open`). A single small Postgres instance
with a modest volume is the only billable resource this phase creates.

## Connecting interactively

```bash
railway connect pgvector   # opens psql against the service
```
