#!/usr/bin/env bash
#
# Build the local Postgres that the test suite runs against.
#
#   bash tools/setup_test_db.sh              # create / update
#   bash tools/setup_test_db.sh --reseed     # also refresh the corpus from Railway
#
# Why local at all: every test opens its own connection, because pytest-asyncio
# gives each test a fresh event loop and a pool cannot cross loops (see
# tests/conftest.py). At ~200 tests that is more connection churn than Railway's
# public TCP proxy will absorb, and the suite fails at fixture setup with
# TimeoutError out of asyncpg.connect() rather than on any assertion. Postgres
# was never the limit: max_connections is 100 and a failing run showed 9 in use.
#
# Postgres 18 and pgvector 0.8.5 are not arbitrary — they are exactly what
# Railway runs (pgvector/pgvector:pg18). The suite leans on generated tsvector
# columns, hnsw indexes, and real constraints, so it is only worth trusting if
# those behave as they do in production.
#
# Port 5433, not 5432, so any Postgres already on this machine keeps its port.
#
# Idempotent; safe to re-run.

set -euo pipefail

PG_VERSION=18
PORT=5433
DB=nhtsa_comms_test
PREFIX="$(brew --prefix)/opt/postgresql@${PG_VERSION}"
PSQL="${PREFIX}/bin/psql"
TEST_URL="postgresql://$(whoami)@localhost:${PORT}/${DB}"

cd "$(dirname "$0")/.."

reseed=false
[[ "${1:-}" == "--reseed" ]] && reseed=true

echo "==> Installing postgresql@${PG_VERSION} and pgvector"
brew list --formula | grep -qx "postgresql@${PG_VERSION}" || brew install "postgresql@${PG_VERSION}"
brew list --formula | grep -qx pgvector || brew install pgvector

echo "==> Pinning postgresql@${PG_VERSION} to port ${PORT}"
sed -i '' -E "s/^#?port = [0-9]+.*/port = ${PORT}/" \
    "$(brew --prefix)/var/postgresql@${PG_VERSION}/postgresql.conf"

echo "==> Starting postgresql@${PG_VERSION}"
brew services start "postgresql@${PG_VERSION}" >/dev/null
for _ in $(seq 1 30); do
    "$PSQL" -p "$PORT" -d postgres -c '' >/dev/null 2>&1 && break
    sleep 1
done

# Seed from Railway on first build, and on --reseed. Three tests in
# test_symptom_retrieval.py and test_usage_ledger.py assert against the real
# corpus by nhtsa_id (for example PI0754G, linked solely to vehicle 218944);
# they are meaningful only against real data, so the local database is a replica
# rather than an empty schema. One connection, so none of the churn above.
exists=$("$PSQL" -p "$PORT" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB}'")
if [[ -z "$exists" || "$reseed" == true ]]; then
    echo "==> Seeding ${DB} from Railway"
    source_url=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
    if [[ -z "$source_url" ]]; then
        echo "DATABASE_URL is not set in backend/.env; cannot seed." >&2
        exit 2
    fi
    dump=$(mktemp -t nhtsa_seed)
    trap 'rm -f "$dump"' EXIT
    "${PREFIX}/bin/pg_dump" "$source_url" --no-owner --no-privileges --format=custom -f "$dump"
    "$PSQL" -p "$PORT" -d postgres -c "DROP DATABASE IF EXISTS ${DB};" >/dev/null
    "$PSQL" -p "$PORT" -d postgres -c "CREATE DATABASE ${DB};" >/dev/null
    "${PREFIX}/bin/pg_restore" --no-owner --no-privileges -d "$TEST_URL" "$dump"
fi

echo "==> Applying migrations"
DATABASE_URL="$TEST_URL" python3 -m src.db.migrate

if ! grep -q '^TEST_DATABASE_URL=' .env 2>/dev/null; then
    cat <<EOF

Add this to backend/.env to point the suite at it:

  TEST_DATABASE_URL=${TEST_URL}
EOF
fi

cat <<EOF

Done. Run the suite:

  cd backend && python3 -m pytest -q
EOF
