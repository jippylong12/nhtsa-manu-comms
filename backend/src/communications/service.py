"""Business logic for Communications feature: the add-vehicle discovery cascade.

Since the plain-nameplate re-key (ADR 0001, M3), all three picker dropdowns are
served from Postgres -- the ``comm_products`` flat-file index -- with **zero NHTSA
calls in the interactive path**. The years, makes and models the picker offers are
exactly the ones NHTSA keys communications to, so a model that shows in the picker
always has bulletins behind it, and a model with none never appears.

This replaces the old byYmmt discovery chain (``/vehicles/*`` crash-test taxonomy +
the ``vehicle_catalog`` cache in front of it), which split every nameplate into
body styles and resolved a numeric ``vehicleId`` through a route NHTSA's gateway
hard-killed at 30s. Discovery can no longer time out or 504: it is a bounded
Postgres query against an index that stays served even when NHTSA is fully down.
The variant/trim resolve step is gone entirely -- association happens on the plain
nameplate at fetch time (see ``src/jobs/sync.py``).
"""

import logging

from src.db import get_pool

log = logging.getLogger("discovery")


class CommunicationService:
    """Discovery for the add-vehicle flow, served from the flat-file index."""

    # Curated set of makes the picker surfaces, intersected with what the index
    # actually holds for the chosen year. Kept as a curation filter over the full
    # (long-tail) make list the flat files contain.
    TOP_MAKES = [
        "AUDI",
        "BMW",
        "BUICK",
        "CADILLAC",
        "CHEVROLET",
        "DODGE",
        "FORD",
        "GMC",
        "HONDA",
        "HYUNDAI",
        "JEEP",
        "KIA",
        "LEXUS",
        "MAZDA",
        "MERCEDES-BENZ",
        "NISSAN",
        "RAM",
        "SUBARU",
        "TESLA",
        "TOYOTA",
        "VOLKSWAGEN",
    ]

    # The picker only needs recent model years; the corpus goes back to 1995.
    _YEAR_LIMIT = 30

    @staticmethod
    async def get_discovery_years() -> list[int]:
        """Model years present in the index, newest first (excludes 9999=unknown)."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT model_year
                FROM comm_products
                WHERE model_year <> 9999
                ORDER BY model_year DESC
                LIMIT $1
                """,
                CommunicationService._YEAR_LIMIT,
            )
        return [r["model_year"] for r in rows]

    @staticmethod
    async def get_discovery_makes(year: int) -> list[str]:
        """Curated makes that have communications for the given year.

        The full make list is intersected with ``TOP_MAKES`` for curation; a make
        with no bulletins that year is not offered. Order follows ``TOP_MAKES``
        (alphabetical), so the dropdown is stable across years.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT make FROM comm_products WHERE model_year = $1",
                int(year),
            )
        present = {r["make"] for r in rows}
        return [m for m in CommunicationService.TOP_MAKES if m in present]

    @staticmethod
    async def get_discovery_models(year: int, make: str) -> list[str]:
        """Plain nameplates that have communications for the given year and make.

        Returns the canonical (normalized) nameplates NHTSA files bulletins under
        -- ``F 150``, ``SILVERADO 1500``, ``MUSTANG MACH E`` -- alphabetically.
        Powertrain/trim variants NHTSA keys separately (``ACCORD HYBRID``,
        ``CIVIC TYPE R``) appear as their own entries, which is correct: they are
        distinct, separately-keyed nameplates.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT model
                FROM comm_products
                WHERE model_year = $1 AND make = $2
                ORDER BY model
                """,
                int(year),
                make.upper(),
            )
        return [r["model"] for r in rows]
