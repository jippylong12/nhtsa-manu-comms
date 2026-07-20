"""Postgres access layer for the Phase A canonical store."""

from src.db.pool import close_pool, get_pool

__all__ = ["get_pool", "close_pool"]
