"""Async Postgres connection pool.

asyncpg rather than SQLAlchemy async: the pipeline and read API are hand-written
SQL against a small, fixed schema with Postgres-specific types (vector, tsvector,
text[]) that an ORM would only get in the way of. asyncpg is also the fastest
driver available and is what SQLAlchemy would sit on top of anyway.
"""

import asyncio
from typing import Optional

import asyncpg

from src.config import get_settings

_pool: Optional[asyncpg.Pool] = None
# Guards pool creation: without it, two coroutines racing on the first call
# (a cold-start request burst) would both build a pool and orphan one, leaking
# its connections since close_pool() only closes the last-assigned pool.
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """Return the process-wide pool, creating it on first use."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            # Re-check inside the lock: another coroutine may have created it
            # while we awaited the lock.
            if _pool is None:
                settings = get_settings()
                if not settings.database_url:
                    raise RuntimeError(
                        "DATABASE_URL is not set. Add it to backend/.env " "(see docs/database.md)."
                    )
                _pool = await asyncpg.create_pool(
                    settings.database_url,
                    min_size=1,
                    max_size=settings.postgres_pool_max,
                    command_timeout=60,
                    # Railway's public TCP proxy drops idle connections; recycle
                    # them before it does so a mid-run query doesn't hit a dead
                    # socket.
                    max_inactive_connection_lifetime=180.0,
                    server_settings={"application_name": "nhtsa-comms"},
                )
    return _pool


async def close_pool() -> None:
    """Dispose of the pool (FastAPI shutdown, or end of a CLI job)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
