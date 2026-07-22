"""Business logic for Communications feature.

Reduced to the NHTSA discovery cascade that fills the add-vehicle form, plus the
vehicle_catalog cache behind it. Everything else here used to be a second,
independent path that fetched the same bulletins into Mongo: a list endpoint, a
detail endpoint, a stats endpoint, and an SSE fetch. The vehicle card counted
that store and the detail page read Postgres, so the two disagreed. See JIP-417.

The product-matching machinery went with it. It existed because a Mongo
communication recorded the vehicle it was fetched for and its own
associatedProducts, and counting a vehicle's bulletins meant reconciling
"F-150 SUPER CREW" against "F-150" with escaped regexes. The corpus has no such
problem: NHTSA already says which bulletins belong to a vehicleId, sync records
that as a communication_vehicles row, and the count is those rows. The property
that commit 47a8611 fixed is now structural rather than computed, and
tests/test_corpus_counts.py holds it.
"""

from datetime import datetime, timezone
from typing import Any

from pymongo import UpdateOne
from pymongo.errors import PyMongoError

from src.communications.nhtsa_client import NHTSAClient, build_vehicle_description
from src.database import get_database


class CommunicationService:
    """Discovery for the add-vehicle flow."""

    # Top 21 US car manufacturers by 2024 sales (sorted alphabetically)
    TOP_MAKES = [
        "AUDI", "BMW", "BUICK", "CADILLAC", "CHEVROLET", "DODGE", "FORD",
        "GMC", "HONDA", "HYUNDAI", "JEEP", "KIA", "LEXUS", "MAZDA",
        "MERCEDES-BENZ", "NISSAN", "RAM", "SUBARU", "TESLA", "TOYOTA", "VOLKSWAGEN",
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
        which uppercase make and model, so the lookup uppercases too. Sorted by
        vehicle_id so a cache hit returns the same order every time.
        """
        db = get_database()
        cursor = db.vehicle_catalog.find(
            {"year": int(year), "make": make.upper(), "model": model.upper()}
        ).sort("vehicle_id", 1)
        return [CommunicationService._catalog_row_to_variant(doc) async for doc in cursor]

    @staticmethod
    async def _cache_variants(
        year: int, make: str, model: str, variants: list[dict[str, Any]]
    ) -> None:
        """Upsert resolved variants into vehicle_catalog, keyed on vehicle_id.

        vehicle_id is NHTSA's globally unique variant key and already carries a
        unique index, so it is the upsert key. Rows match the shape written by
        seed_vehicle_catalog.py.

        Writing is best effort. Concurrent upserts of the same vehicle_id race on
        the unique index and raise a duplicate-key error, and that must not fail
        a request whose variants have already been resolved.
        """
        operations = []
        for variant in variants:
            vehicle_id = variant.get("vehicleId")
            if not vehicle_id:
                continue
            operations.append(
                UpdateOne(
                    {"vehicle_id": vehicle_id},
                    {
                        "$set": {
                            "vehicle_id": vehicle_id,
                            "ncap_id": variant.get("ncapId"),
                            "year": int(year),
                            "make": make.upper(),
                            "model": (variant.get("model") or model).upper(),
                            "trim": variant.get("trim"),
                            "series": variant.get("series"),
                            "cached_at": datetime.now(timezone.utc),
                        }
                    },
                    upsert=True,
                )
            )

        if not operations:
            return

        db = get_database()
        try:
            await db.vehicle_catalog.bulk_write(operations, ordered=False)
        except PyMongoError as e:
            print(f"vehicle_catalog cache write skipped for {year} {make} {model}: {e}")

    @staticmethod
    async def get_discovery_variants(
        year: int, make: str, model: str, trim: str | None = None
    ) -> list[dict[str, Any]]:
        """Get vehicle variants with correct vehicleId, cached in vehicle_catalog.

        The first request for a year/make/model pays the NHTSA byYmmt round-trip
        and populates the cache; every later request is served from Mongo. The
        cache always holds the complete variant set for a YMM, so an optional
        trim filter is applied in memory rather than by NHTSA. byYmmt matches
        trim case-insensitively, so this does too.
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


