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

    # vehicle_catalog is the only collection anything still reads. The vehicles,
    # communications and searches indexes went with the endpoints that used
    # them (JIP-416, JIP-417); building indexes for collections nothing queries
    # is startup cost for nothing. This whole module goes in JIP-421.
    #
    # Index names match the ones seed_vehicle_catalog.py creates so warm-started
    # catalogs stay compatible.
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
