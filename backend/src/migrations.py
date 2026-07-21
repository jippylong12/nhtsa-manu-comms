"""Database migrations for backfilling data."""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from src.config import get_settings
from src.communications.schemas import get_comm_type
from src.communications.service import CommunicationService


async def backfill_communication_types():
    """Backfill communication_type field for existing records using enhanced detection."""
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_database]
    
    # Find all communications
    cursor = db.communications.find({})
    
    updated = 0
    async for doc in cursor:
        comm_number = doc.get("communication_number")
        summary = doc.get("summary") or doc.get("details_summary") or ""
        documents = doc.get("associated_documents") or []
        
        # Use enhanced type detection with summary and documents
        new_type = get_comm_type(comm_number, summary, documents)
        
        # Only update if different
        current_type = doc.get("communication_type")
        if current_type != new_type:
            await db.communications.update_one(
                {"_id": doc["_id"]},
                {"$set": {"communication_type": new_type}}
            )
            updated += 1
        
        if updated % 100 == 0 and updated > 0:
            print(f"Updated {updated} records...")
    
    print(f"Backfill complete. Updated {updated} records.")
    client.close()


async def backfill_comm_counts():
    """Recount comm_count for every vehicle using the current matching rules.

    Vehicles whose model name carries a cab or body style, such as "F-150 SUPER
    CREW", were counted with a matcher that required the bulletin's product
    model to be identical. NHTSA names that product "F-150", so those vehicles
    stored comm_count 0 even though every communication had been fetched. The
    count is only refreshed on fetch, so existing records stay wrong until this
    recounts them.
    """
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_database]

    updated = 0
    async for vehicle in db.vehicles.find({}):
        vehicle_id = vehicle.get("vehicle_id")
        if vehicle_id is None:
            continue

        query = CommunicationService._product_match_query(
            vehicle_id, vehicle.get("year") or "", vehicle.get("model") or ""
        )
        recounted = await db.communications.count_documents(query)

        if recounted != vehicle.get("comm_count"):
            await db.vehicles.update_one(
                {"_id": vehicle["_id"]}, {"$set": {"comm_count": recounted}}
            )
            updated += 1
            print(
                f"  {vehicle.get('year')} {vehicle.get('model')}: "
                f"{vehicle.get('comm_count')} -> {recounted}"
            )

    print(f"Backfill complete. Updated {updated} vehicles.")
    client.close()


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "communication_types"
    if target == "comm_counts":
        asyncio.run(backfill_comm_counts())
    else:
        asyncio.run(backfill_communication_types())
