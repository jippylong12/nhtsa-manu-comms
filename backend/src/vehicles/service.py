"""Business logic for Vehicles feature."""

from datetime import datetime, timezone
from typing import Any, Optional
from bson import ObjectId

from src.database import get_database


class VehicleService:
    """Service layer for vehicle operations."""

    @staticmethod
    async def create_vehicle(
        vehicle_id: int,
        year: str,
        model: str,
        keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new vehicle to track."""
        db = get_database()
        now = datetime.now(timezone.utc)

        document = {
            "vehicle_id": vehicle_id,
            "year": year,
            "model": model.upper(),
            "keywords": keywords or [],
            "last_fetched": None,
            "comm_count": 0,
            "created_at": now,
            "updated_at": now,
        }

        # Upsert to handle duplicates gracefully
        result = await db.vehicles.update_one(
            {"vehicle_id": vehicle_id},
            {"$setOnInsert": document},
            upsert=True,
        )

        if result.upserted_id:
            document["_id"] = str(result.upserted_id)
        else:
            # Already exists, fetch it
            existing = await db.vehicles.find_one({"vehicle_id": vehicle_id})
            if existing:
                existing["_id"] = str(existing["_id"])
                return existing

        return document

    @staticmethod
    async def get_vehicle(vehicle_id: int) -> Optional[dict[str, Any]]:
        """Get a vehicle by its NHTSA ID."""
        db = get_database()
        vehicle = await db.vehicles.find_one({"vehicle_id": vehicle_id})
        if vehicle:
            vehicle["_id"] = str(vehicle["_id"])
        return vehicle

    @staticmethod
    async def get_vehicle_by_id(doc_id: str) -> Optional[dict[str, Any]]:
        """Get a vehicle by its MongoDB document ID."""
        db = get_database()
        vehicle = await db.vehicles.find_one({"_id": ObjectId(doc_id)})
        if vehicle:
            vehicle["_id"] = str(vehicle["_id"])
        return vehicle

    @staticmethod
    async def list_vehicles(
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List all tracked vehicles with pagination."""
        db = get_database()
        skip = (page - 1) * per_page

        cursor = db.vehicles.find().sort("created_at", -1).skip(skip).limit(per_page)
        vehicles = []
        async for vehicle in cursor:
            vehicle["_id"] = str(vehicle["_id"])
            vehicles.append(vehicle)

        total = await db.vehicles.count_documents({})
        return vehicles, total

    @staticmethod
    async def update_vehicle(
        vehicle_id: int,
        year: Optional[str] = None,
        model: Optional[str] = None,
        keywords: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Update a vehicle's tracking configuration."""
        db = get_database()
        update_fields: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}

        if year is not None:
            update_fields["year"] = year
        if model is not None:
            update_fields["model"] = model.upper()
        if keywords is not None:
            update_fields["keywords"] = keywords

        result = await db.vehicles.find_one_and_update(
            {"vehicle_id": vehicle_id},
            {"$set": update_fields},
            return_document=True,
        )
        if result:
            result["_id"] = str(result["_id"])
        return result

    @staticmethod
    async def delete_vehicle(vehicle_id: int) -> bool:
        """Delete a vehicle and its associated communications."""
        db = get_database()
        result = await db.vehicles.delete_one({"vehicle_id": vehicle_id})
        # Also delete associated communications
        await db.communications.delete_many({"vehicle_id": vehicle_id})
        return result.deleted_count > 0

    @staticmethod
    async def update_fetch_stats(
        vehicle_id: int,
        comm_count: int,
    ) -> None:
        """Update the fetch statistics for a vehicle."""
        db = get_database()
        await db.vehicles.update_one(
            {"vehicle_id": vehicle_id},
            {
                "$set": {
                    "last_fetched": datetime.now(timezone.utc),
                    "comm_count": comm_count,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
