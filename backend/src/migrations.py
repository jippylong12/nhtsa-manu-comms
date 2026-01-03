"""Database migrations for backfilling data."""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from src.config import get_settings
from src.communications.schemas import get_comm_type


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


if __name__ == "__main__":
    asyncio.run(backfill_communication_types())
