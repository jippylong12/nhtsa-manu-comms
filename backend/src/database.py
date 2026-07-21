"""MongoDB database connection using Motor (async driver)."""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from typing import Optional

from src.config import get_settings


class Database:
    """MongoDB database connection manager."""

    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None


database = Database()


async def connect_to_mongodb() -> None:
    """Establish connection to MongoDB."""
    settings = get_settings()
    database.client = AsyncIOMotorClient(settings.mongodb_url)
    database.db = database.client[settings.mongodb_database]

    # Create indexes for efficient queries
    await database.db.vehicles.create_index("vehicle_id", unique=True)
    await database.db.communications.create_index(
        [("nhtsa_id", 1), ("vehicle_id", 1)], unique=True, name="nhtsa_id_vehicle_id_unique"
    )
    await database.db.communications.create_index("vehicle_id")
    await database.db.communications.create_index("communication_date")
    await database.db.searches.create_index("created_at")
    # vehicle_catalog is the YMM -> vehicleId cache. Index names match the ones
    # seed_vehicle_catalog.py creates so warm-started catalogs stay compatible.
    await database.db.vehicle_catalog.create_index("vehicle_id", unique=True)
    await database.db.vehicle_catalog.create_index([("year", 1), ("make", 1), ("model", 1)])

    print(f"Connected to MongoDB: {settings.mongodb_database}")


async def close_mongodb_connection() -> None:
    """Close MongoDB connection."""
    if database.client:
        database.client.close()
        print("Closed MongoDB connection")


def get_database() -> AsyncIOMotorDatabase:
    """Get the database instance."""
    if database.db is None:
        raise RuntimeError("Database not initialized. Call connect_to_mongodb() first.")
    return database.db
