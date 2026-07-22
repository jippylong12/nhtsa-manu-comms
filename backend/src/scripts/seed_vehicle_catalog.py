#!/usr/bin/env python -u
"""Warm-start the YMM to vehicleId cache, using curl to avoid WAF blocking.

Optional. `get_discovery_variants` fills the cache on demand, so this only
front-loads the first round trip for every year/make/model combination the
add-vehicle form can reach. Worth running on a fresh database, pointless
otherwise.

Writes Postgres since JIP-420.
"""

import asyncio
import json
import subprocess
import sys

import asyncpg

from src.config import get_settings

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

BASE_URL = "https://api.nhtsa.gov"
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
YEARS = list(range(2026, 2014, -1))

UPSERT = """
    INSERT INTO vehicle_catalog (vehicle_id, ncap_id, year, make, model, trim, series)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (vehicle_id) DO UPDATE SET
        ncap_id   = EXCLUDED.ncap_id,
        year      = EXCLUDED.year,
        make      = EXCLUDED.make,
        model     = EXCLUDED.model,
        trim      = EXCLUDED.trim,
        series    = EXCLUDED.series,
        cached_at = now()
"""


def curl_get(url: str) -> dict | None:
    """Use curl to fetch JSON - avoids Akamai WAF blocking."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-H", "User-Agent: Mozilla/5.0", url],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except Exception as e:
        print(f"curl error: {e}", file=sys.stderr)
    return None


async def main():
    settings = get_settings()
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set. See docs/database.md.")
    conn = await asyncpg.connect(settings.database_url)

    # Upserted rather than cleared: a run that dies partway through should leave
    # the cache warmer than it found it, not empty.
    print("📇 Seeding catalog (existing rows are refreshed, not dropped)\n")
    sys.stdout.flush()

    total = 0
    for year in YEARS:
        print(f"📅 {year}: ", end="")
        sys.stdout.flush()
        year_entries = []

        for make in TOP_MAKES:
            # Get models for this year/make
            url = f"{BASE_URL}/vehicles/models?modelYear={year}&make={make}&max=100"
            data = curl_get(url)
            if not data:
                print("x", end="")
                sys.stdout.flush()
                continue

            models = list(
                set(m["vehicleModel"] for m in data.get("results", []) if m.get("vehicleModel"))
            )

            # Get variants for each model
            for model in models:
                model_encoded = model.replace(" ", "%20")
                url = (
                    f"{BASE_URL}/vehicles/byYmmt?modelYear={year}&make={make}&model={model_encoded}"
                )
                data = curl_get(url)
                if not data:
                    continue

                for v in data.get("results", []):
                    if v.get("vehicleId"):
                        year_entries.append(
                            (
                                v["vehicleId"],
                                v.get("ncapId"),
                                year,
                                make.upper(),
                                (v.get("vehicleModel") or model).upper(),
                                v.get("trim"),
                                v.get("series"),
                            )
                        )
            print(".", end="")
            sys.stdout.flush()

        # Batch upsert for this year. Deduplicated first: byYmmt can return the
        # same vehicleId under two model spellings, and executemany would abort
        # the whole year on the first collision.
        if year_entries:
            deduped = list({e[0]: e for e in year_entries}.values())
            await conn.executemany(UPSERT, deduped)
            total += len(deduped)
        print(f" {len(year_entries)} vehicles")
        sys.stdout.flush()

    # No index creation here: the schema owns that, in migrations/0006.
    print(f"✅ Done! {total:,} vehicles seeded")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
