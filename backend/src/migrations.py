"""Database migrations for backfilling data."""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from src.config import get_settings
from src.communications.schemas import get_comm_type


async def backfill_communication_types():
    """Backfill communication_type field for existing records."""
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_database]
    
    # Find all communications without communication_type or with null/OTHER
    cursor = db.communications.find({})
    
    updated = 0
    async for doc in cursor:
        comm_number = doc.get("communication_number")
        comm_type = get_comm_type(comm_number)
        
        # Only update if different or missing
        current_type = doc.get("communication_type")
        if current_type != comm_type:
            await db.communications.update_one(
                {"_id": doc["_id"]},
                {"$set": {"communication_type": comm_type}}
            )
            updated += 1
        
        if updated % 100 == 0 and updated > 0:
            print(f"Updated {updated} records...")
    
    print(f"Backfill complete. Updated {updated} records.")
    client.close()


if __name__ == "__main__":
    asyncio.run(backfill_communication_types())
