"""Migration runner for the canonical Postgres schema.

Plain ordered SQL files rather than Alembic: this is a solo project with a
single linear schema history and no ORM models to autogenerate from, so the
extra moving parts buy nothing. Each file in `backend/migrations/*.sql` runs
once, in filename order, inside a transaction, and is recorded in
`schema_migrations`. Re-running is a no-op.

Usage:
    python -m src.db.migrate          # apply pending migrations
    python -m src.db.migrate --status # show applied/pending without applying
"""

import argparse
import asyncio
import hashlib
import pathlib
import sys

import asyncpg

from src.config import get_settings

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "migrations"

CREATE_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text        PRIMARY KEY,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def discover() -> list[pathlib.Path]:
    """Return migration files in lexical order (zero-padded numeric prefixes)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


async def _connect() -> asyncpg.Connection:
    settings = get_settings()
    if not settings.database_url:
        print(
            "DATABASE_URL is not set. Add it to backend/.env " "(see docs/database.md).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return await asyncpg.connect(settings.database_url)


async def status() -> int:
    conn = await _connect()
    try:
        await conn.execute(CREATE_TRACKING_TABLE)
        applied = {r["version"]: r for r in await conn.fetch("SELECT * FROM schema_migrations")}
        for path in discover():
            version = path.stem
            row = applied.get(version)
            if row is None:
                print(f"  pending  {version}")
            elif row["checksum"] != checksum(path):
                print(f"  CHANGED  {version}  (already applied, file has since been edited)")
            else:
                print(f"  applied  {version}  {row['applied_at']:%Y-%m-%d %H:%M:%S}")
        return 0
    finally:
        await conn.close()


async def migrate() -> int:
    conn = await _connect()
    try:
        await conn.execute(CREATE_TRACKING_TABLE)
        applied = {
            r["version"]: r["checksum"] for r in await conn.fetch("SELECT * FROM schema_migrations")
        }

        pending = [p for p in discover() if p.stem not in applied]

        # A migration that was edited after being applied is a silent
        # divergence between the file and the live database. Refuse rather
        # than pretend the schema matches the repo.
        for path in discover():
            if path.stem in applied and applied[path.stem] != checksum(path):
                print(
                    f"ERROR: {path.name} was modified after it was applied. "
                    f"Add a new migration instead of editing an applied one.",
                    file=sys.stderr,
                )
                return 1

        if not pending:
            print("No pending migrations.")
            return 0

        for path in pending:
            print(f"applying {path.name} ...", end=" ", flush=True)
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    path.stem,
                    checksum(path),
                )
            print("ok")

        print(f"Applied {len(pending)} migration(s).")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Postgres migrations.")
    parser.add_argument("--status", action="store_true", help="show state without applying")
    args = parser.parse_args()
    return asyncio.run(status() if args.status else migrate())


if __name__ == "__main__":
    raise SystemExit(main())
