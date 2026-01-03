#!/usr/bin/env python -u
"""Seed vehicle catalog using curl to avoid WAF blocking."""

import sys
import asyncio
import subprocess
import json
from motor.motor_asyncio import AsyncIOMotorClient
from src.config import get_settings

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

BASE_URL = "https://api.nhtsa.gov"
TOP_MAKES = ["AUDI", "BMW", "BUICK", "CADILLAC", "CHEVROLET", "DODGE", "FORD",
    "GMC", "HONDA", "HYUNDAI", "JEEP", "KIA", "LEXUS", "MAZDA",
    "MERCEDES-BENZ", "NISSAN", "RAM", "SUBARU", "TESLA", "TOYOTA", "VOLKSWAGEN"]
YEARS = list(range(2026, 2014, -1))


def curl_get(url: str) -> dict | None:
    """Use curl to fetch JSON - avoids Akamai WAF blocking."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-H", "User-Agent: Mozilla/5.0", url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except Exception as e:
        print(f"curl error: {e}", file=sys.stderr)
    return None


async def main():
    settings = get_settings()
    mongo = AsyncIOMotorClient(settings.mongodb_url)
    db = mongo[settings.mongodb_database]
    
    print("🗑️  Clearing catalog...")
    sys.stdout.flush()
    await db.vehicle_catalog.delete_many({})
    print("   Done\n")
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
            
            models = list(set(m["vehicleModel"] for m in data.get("results", []) if m.get("vehicleModel")))
            
            # Get variants for each model
            for model in models:
                model_encoded = model.replace(" ", "%20")
                url = f"{BASE_URL}/vehicles/byYmmt?modelYear={year}&make={make}&model={model_encoded}"
                data = curl_get(url)
                if not data:
                    continue
                
                for v in data.get("results", []):
                    if v.get("vehicleId"):
                        year_entries.append({
                            "vehicle_id": v["vehicleId"],
                            "ncap_id": v.get("ncapId"),
                            "year": year,
                            "make": make.upper(),
                            "model": (v.get("vehicleModel") or model).upper(),
                            "trim": v.get("trim"),
                            "series": v.get("series"),
                        })
            print(".", end="")
            sys.stdout.flush()
        
        # Batch insert for this year
        if year_entries:
            await db.vehicle_catalog.insert_many(year_entries)
            total += len(year_entries)
        print(f" {len(year_entries)} vehicles")
        sys.stdout.flush()
    
    print("\n📊 Creating indexes...")
    sys.stdout.flush()
    await db.vehicle_catalog.create_index("year")
    await db.vehicle_catalog.create_index([("year", 1), ("make", 1)])
    await db.vehicle_catalog.create_index([("year", 1), ("make", 1), ("model", 1)])
    await db.vehicle_catalog.create_index("vehicle_id", unique=True)
    
    print(f"✅ Done! {total:,} vehicles in catalog")
    mongo.close()

if __name__ == "__main__":
    asyncio.run(main())
