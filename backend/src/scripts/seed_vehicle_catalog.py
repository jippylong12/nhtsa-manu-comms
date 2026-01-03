"""Seed the vehicle catalog for top 20 US manufacturers.

Two-step process:
1. Get models for each make using /vehicles/models
2. Get variants for each model using /vehicles/byYmmt

Usage:
    python -m src.scripts.seed_vehicle_catalog
"""

import asyncio
import httpx
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient

from src.config import get_settings


BASE_URL = "https://api.nhtsa.gov"
HEADERS = {
    "User-Agent": "nhtsa-manu-comms/2.0 (+https://nhtsa.gov)",
    "Accept": "application/json",
}

# Top 20 US car manufacturers by 2024 sales + Tesla
TOP_MAKES = [
    "TOYOTA", "FORD", "CHEVROLET", "HONDA", "NISSAN", "HYUNDAI", "KIA",
    "SUBARU", "GMC", "JEEP", "RAM", "MAZDA", "VOLKSWAGEN", "BMW", "LEXUS",
    "MERCEDES-BENZ", "AUDI", "BUICK", "CADILLAC", "DODGE", "TESLA",
]

# Years to seed
YEARS = list(range(2026, 2014, -1))  # 2026 down to 2015


async def seed_catalog():
    """Seed the vehicle catalog for top makes only."""
    settings = get_settings()
    
    mongo_client = AsyncIOMotorClient(settings.mongodb_url)
    db = mongo_client[settings.mongodb_database]
    catalog = db.vehicle_catalog
    
    print("🗑️  Clearing existing catalog...")
    await catalog.delete_many({})
    
    total_inserted = 0
    
    async with httpx.AsyncClient(timeout=60.0, headers=HEADERS) as client:
        for year in YEARS:
            print(f"\n📅 Year {year}")
            
            for make in TOP_MAKES:
                # Step 1: Get all models for this year/make
                try:
                    response = await client.get(
                        f"{BASE_URL}/vehicles/models",
                        params={"modelYear": year, "make": make}
                    )
                    
                    if response.status_code != 200:
                        continue
                    
                    models_data = response.json().get("results", [])
                    models = list(set(m.get("vehicleModel") for m in models_data if m.get("vehicleModel")))
                    
                    if not models:
                        continue
                    
                    make_count = 0
                    
                    # Step 2: Get variants for each model
                    for model in models:
                        try:
                            response = await client.get(
                                f"{BASE_URL}/vehicles/byYmmt",
                                params={"modelYear": year, "make": make, "model": model}
                            )
                            
                            if response.status_code != 200:
                                continue
                            
                            variants = response.json().get("results", [])
                            
                            entries = []
                            for v in variants:
                                vehicle_id = v.get("vehicleId")
                                if not vehicle_id:
                                    continue
                                
                                entries.append({
                                    "vehicle_id": vehicle_id,
                                    "ncap_id": v.get("ncapId"),
                                    "year": v.get("modelYear"),
                                    "make": (v.get("make") or "").upper(),
                                    "model": (v.get("vehicleModel") or "").upper(),
                                    "trim": v.get("trim"),
                                    "series": v.get("series"),
                                    "seeded_at": datetime.now(timezone.utc),
                                })
                            
                            if entries:
                                await catalog.insert_many(entries)
                                make_count += len(entries)
                                total_inserted += len(entries)
                            
                            await asyncio.sleep(0.02)
                            
                        except Exception:
                            continue
                    
                    if make_count > 0:
                        print(f"   {make}: {make_count} vehicles ({len(models)} models)")
                    
                    await asyncio.sleep(0.02)
                    
                except Exception as e:
                    print(f"   {make}: error - {e}")
                    continue
        
        # Create indexes
        print("\n📊 Creating indexes...")
        await catalog.create_index("year")
        await catalog.create_index("make")
        await catalog.create_index([("year", 1), ("make", 1)])
        await catalog.create_index([("year", 1), ("make", 1), ("model", 1)])
        await catalog.create_index("vehicle_id", unique=True)
        
        # Summary
        distinct_years = len(await catalog.distinct("year"))
        distinct_makes = len(await catalog.distinct("make"))
        distinct_models = len(await catalog.distinct("model"))
        
        print(f"\n✅ Done!")
        print(f"   Vehicles: {total_inserted:,}")
        print(f"   Years: {distinct_years}")
        print(f"   Makes: {distinct_makes}")
        print(f"   Models: {distinct_models}")
    
    mongo_client.close()


if __name__ == "__main__":
    asyncio.run(seed_catalog())
