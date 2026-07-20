"""Async Postgres connection pool.

asyncpg rather than SQLAlchemy async: the pipeline and read API are hand-written
SQL against a small, fixed schema with Postgres-specific types (vector, tsvector,
text[]) that an ORM would only get in the way of. asyncpg is also the fastest
driver available and is what SQLAlchemy would sit on top of anyway.
"""

from typing import Optional

import asyncpg

from src.config import get_settings

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Return the process-wide pool, creating it on first use."""
    global _pool
    if _pool is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Add it to backend/.env (see docs/database.md)."
            )
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=settings.postgres_pool_max,
            command_timeout=60,
            # Railway routes through a public TCP proxy; a keepalive avoids
            # idle connections being dropped mid-run by the proxy.
            server_settings={"application_name": "nhtsa-comms"},
        )
    return _pool


async def close_pool() -> None:
    """Dispose of the pool (FastAPI shutdown, or end of a CLI job)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
