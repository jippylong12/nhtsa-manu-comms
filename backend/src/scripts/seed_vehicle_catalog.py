"""Fast parallel seed with proper rate limiting to avoid 403."""

import asyncio
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from src.config import get_settings

BASE_URL = "https://api.nhtsa.gov"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}
TOP_MAKES = ["TOYOTA", "FORD", "CHEVROLET", "HONDA", "NISSAN", "HYUNDAI", "KIA",
    "SUBARU", "GMC", "JEEP", "RAM", "MAZDA", "VOLKSWAGEN", "BMW", "LEXUS",
    "MERCEDES-BENZ", "AUDI", "BUICK", "CADILLAC", "DODGE", "TESLA"]
YEARS = list(range(2026, 2014, -1))


async def main():
    settings = get_settings()
    mongo = AsyncIOMotorClient(settings.mongodb_url)
    db = mongo[settings.mongodb_database]
    
    sem = asyncio.Semaphore(10)  # Conservative to avoid 403
    
    async def fetch(client, url, params):
        async with sem:
            await asyncio.sleep(0.05)  # Small delay between requests
            r = await client.get(url, params=params)
            if r.status_code == 403:
                print(f"   ⚠️ 403 - rate limited")
                return None
            return r if r.status_code == 200 else None
    
    async def fetch_make(client, year, make):
        r = await fetch(client, f"{BASE_URL}/vehicles/models", {"modelYear": year, "make": make, "max": 100})
        if not r:
            return []
        models = list(set(m["vehicleModel"] for m in r.json().get("results", []) if m.get("vehicleModel")))
        
        async def get_variants(model):
            r = await fetch(client, f"{BASE_URL}/vehicles/byYmmt", {"modelYear": year, "make": make, "model": model})
            if not r:
                return []
            return [{"vehicle_id": v["vehicleId"], "ncap_id": v.get("ncapId"), "year": year,
                    "make": make.upper(), "model": (v.get("vehicleModel") or model).upper(),
                    "trim": v.get("trim"), "series": v.get("series")} 
                   for v in r.json().get("results", []) if v.get("vehicleId")]
        
        results = await asyncio.gather(*[get_variants(m) for m in models])
        return [v for r in results for v in r]
    
    print("🗑️  Clearing...")
    await db.vehicle_catalog.delete_many({})
    
    total = 0
    async with httpx.AsyncClient(timeout=60.0, headers=HEADERS, limits=httpx.Limits(max_connections=15)) as client:
        for year in YEARS:
            results = await asyncio.gather(*[fetch_make(client, year, make) for make in TOP_MAKES])
            entries = [v for r in results for v in r]
            
            if entries:
                await db.vehicle_catalog.insert_many(entries)
                total += len(entries)
            print(f"📅 {year}: {len(entries)}")
        
        print("📊 Indexing...")
        await db.vehicle_catalog.create_index("year")
        await db.vehicle_catalog.create_index([("year", 1), ("make", 1)])
        await db.vehicle_catalog.create_index([("year", 1), ("make", 1), ("model", 1)])
        await db.vehicle_catalog.create_index("vehicle_id", unique=True)
        print(f"✅ Done! {total:,} vehicles")
    mongo.close()

if __name__ == "__main__":
    asyncio.run(main())
