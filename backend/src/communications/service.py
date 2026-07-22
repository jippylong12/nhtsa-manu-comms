"""Business logic for Communications feature.

Reduced to the NHTSA discovery cascade that fills the add-vehicle form, plus the
vehicle_catalog cache behind it, which moved to Postgres in JIP-420.

Everything else here used to be a second, independent path that fetched the same
bulletins into Mongo: a list endpoint, a detail endpoint, a stats endpoint, and
an SSE fetch. The vehicle card counted that store and the detail page read
Postgres, so the two disagreed. See JIP-417.

The product-matching machinery went with it. It existed because a Mongo
communication recorded the vehicle it was fetched for and its own
associatedProducts, and counting a vehicle's bulletins meant reconciling
"F-150 SUPER CREW" against "F-150" with escaped regexes. The corpus has no such
problem: NHTSA already says which bulletins belong to a vehicleId, sync records
that as a communication_vehicles row, and the count is those rows. The property
that commit 47a8611 fixed is now structural rather than computed, and
tests/test_corpus_counts.py holds it.
"""

import logging
from typing import Any

from src.communications.nhtsa_client import NHTSAClient, build_vehicle_description
from src.db import get_pool

log = logging.getLogger("discovery")


class CommunicationService:
    """Discovery for the add-vehicle flow."""

    # Top 21 US car manufacturers by 2024 sales (sorted alphabetically)
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

    @staticmethod
    async def get_discovery_years() -> list[int]:
        """Return recent model years (hardcoded - no API call needed)."""
        current_year = 2026  # NHTSA has data through 2026
        return list(range(current_year, current_year - 20, -1))

    @staticmethod
    async def get_discovery_makes(year: int) -> list[str]:
        """Return top 20 US makes (hardcoded - no API call needed)."""
        # Just return the hardcoded list - these are available for all years
        return CommunicationService.TOP_MAKES

    @staticmethod
    async def get_discovery_models(year: int, make: str) -> list[str]:
        """Get models for a year and make from NHTSA API."""
        client = NHTSAClient()
        return await client.get_models_for_make_year(year, make)

    @staticmethod
    async def get_discovery_trims(year: int, make: str, model: str) -> list[str]:
        """Get available trims for a year/make/model from NHTSA API."""
        client = NHTSAClient()
        return await client.get_trims_for_model(year, make, model)

    @staticmethod
    def _catalog_row_to_variant(doc: dict[str, Any]) -> dict[str, Any]:
        """Map a vehicle_catalog row onto the byYmmt response shape."""
        return {
            "vehicleId": doc.get("vehicle_id"),
            "ncapId": doc.get("ncap_id"),
            "modelYear": doc.get("year"),
            "make": doc.get("make"),
            "model": doc.get("model"),
            "trim": doc.get("trim"),
            "series": doc.get("series"),
            "vehicleDescription": build_vehicle_description(
                doc.get("model"), doc.get("trim"), doc.get("series")
            ),
        }

    @staticmethod
    async def _read_cached_variants(year: int, make: str, model: str) -> list[dict[str, Any]]:
        """Read every cached variant for a year/make/model, or [] on a cache miss.

        Rows are written by this service and by seed_vehicle_catalog.py, both of
        which uppercase make and model, so the lookup uppercases too. Ordered by
        vehicle_id so a cache hit returns the same order every time.
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT vehicle_id, ncap_id, year, make, model, trim, series
                FROM vehicle_catalog
                WHERE year = $1 AND make = $2 AND model = $3
                ORDER BY vehicle_id
                """,
                int(year),
                make.upper(),
                model.upper(),
            )
        return [CommunicationService._catalog_row_to_variant(dict(r)) for r in rows]

    @staticmethod
    async def _cache_variants(
        year: int, make: str, model: str, variants: list[dict[str, Any]]
    ) -> None:
        """Upsert resolved variants into vehicle_catalog, keyed on vehicle_id.

        vehicle_id is NHTSA's globally unique variant key and the table's primary
        key, so it is the upsert target.

        Writing is best effort. Two requests resolving the same YMM at once race
        on the primary key, and losing that race must not fail a request whose
        variants have already been resolved. The caller has its answer either
        way; the cache is an optimisation, not the source of truth.
        """
        rows = [
            (
                variant["vehicleId"],
                variant.get("ncapId"),
                int(year),
                make.upper(),
                (variant.get("model") or model).upper(),
                variant.get("trim"),
                variant.get("series"),
            )
            for variant in variants
            if variant.get("vehicleId")
        ]
        if not rows:
            return

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO vehicle_catalog
                        (vehicle_id, ncap_id, year, make, model, trim, series)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (vehicle_id) DO UPDATE SET
                        ncap_id   = EXCLUDED.ncap_id,
                        year      = EXCLUDED.year,
                        make      = EXCLUDED.make,
                        model     = EXCLUDED.model,
                        trim      = EXCLUDED.trim,
                        series    = EXCLUDED.series,
                        cached_at = now()
                    """,
                    rows,
                )
        except Exception as e:  # noqa: BLE001 - a cache write can never fail a read
            log.warning(
                "vehicle_catalog cache write skipped for %s %s %s (%s: %s)",
                year,
                make,
                model,
                type(e).__name__,
                e,
            )

    @staticmethod
    async def get_discovery_variants(
        year: int, make: str, model: str, trim: str | None = None
    ) -> list[dict[str, Any]]:
        """Get vehicle variants with correct vehicleId, cached in vehicle_catalog.

        The first request for a year/make/model pays the NHTSA byYmmt round-trip
        and populates the cache; every later request is served from Postgres. The
        cache always holds the complete variant set for a YMM, so an optional
        trim filter is applied in memory rather than by NHTSA. byYmmt matches
        trim case-insensitively, so this does too.

        An empty live result is deliberately not cached: a YMM NHTSA does not
        know today may be known next month, and a cached emptiness is
        indistinguishable from a cache miss on read, so it would pin the answer
        forever.
        """
        variants = await CommunicationService._read_cached_variants(year, make, model)

        if not variants:
            client = NHTSAClient()
            variants = await client.get_vehicle_variants(year, make, model)
            await CommunicationService._cache_variants(year, make, model, variants)

        if trim:
            wanted = trim.casefold()
            variants = [v for v in variants if (v.get("trim") or "").casefold() == wanted]

        return variants
