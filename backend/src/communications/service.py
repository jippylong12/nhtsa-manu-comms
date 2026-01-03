"""Business logic for Communications feature."""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, AsyncGenerator, Optional
from bson import ObjectId

from src.database import get_database
from src.vehicles.service import VehicleService
from src.communications.nhtsa_client import (
    NHTSAClient,
    extract_comm_ids_from_details,
    extract_id_to_summary,
    extract_id_to_comm_number,
)
from src.communications.schemas import get_comm_type, COMM_TYPE_MAP


class CommunicationService:
    """Service layer for communication operations."""

    @staticmethod
    def _product_matches(comm: dict[str, Any], year: str, model: str) -> bool:
        """Check if communication references the target product."""
        products = comm.get("associatedProducts") or []
        for p in products:
            p_year = str(p.get("productYear", "")).strip()
            p_model = str(p.get("productModel", "")).strip()
            if p_year == year and p_model.upper() == model.upper():
                return True
        return False

    @staticmethod
    def _matches_keywords(summary: Optional[str], keywords: list[str]) -> list[str]:
        """Return list of matched keywords from summary."""
        if not summary or not keywords:
            return []

        words = {w.strip().lower() for w in str(summary).split() if w.strip()}
        matched = []
        for kw in keywords:
            if kw.lower() in words:
                matched.append(kw)
        return matched

    @staticmethod
    async def get_communication(nhtsa_id: int) -> Optional[dict[str, Any]]:
        """Get a communication by NHTSA ID."""
        db = get_database()
        comm = await db.communications.find_one({"nhtsa_id": nhtsa_id})
        if comm:
            comm["_id"] = str(comm["_id"])
        return comm

    @staticmethod
    async def list_communications(
        vehicle_id: Optional[int] = None,
        year: Optional[str] = None,
        model: Optional[str] = None,
        keywords: Optional[list[str]] = None,
        search: Optional[str] = None,
        comm_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List communications with optional filters."""
        db = get_database()
        skip = (page - 1) * per_page

        query: dict[str, Any] = {}
        if vehicle_id:
            query["vehicle_id"] = vehicle_id
        if year:
            query["associated_products.product_year"] = year
        if model:
            query["associated_products.product_model"] = {"$regex": model, "$options": "i"}
        if keywords:
            query["matched_keywords"] = {"$in": keywords}
        if search:
            query["$or"] = [
                {"summary": {"$regex": search, "$options": "i"}},
                {"communication_number": {"$regex": search, "$options": "i"}},
            ]
        if comm_type:
            # Support comma-separated multiple types
            types = [t.strip().upper() for t in comm_type.split(",") if t.strip()]
            if len(types) == 1:
                query["communication_type"] = types[0]
            elif len(types) > 1:
                query["communication_type"] = {"$in": types}

        cursor = (
            db.communications.find(query)
            .sort("communication_date", -1)
            .skip(skip)
            .limit(per_page)
        )

        comms = []
        async for comm in cursor:
            comm["_id"] = str(comm["_id"])
            comms.append(comm)

        total = await db.communications.count_documents(query)
        return comms, total

    @staticmethod
    async def fetch_and_store(
        vehicle_id: int,
        force_refresh: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Fetch communications from NHTSA and store in MongoDB with progress updates."""
        import time

        start_time = time.time()
        db = get_database()
        client = NHTSAClient()

        # Get vehicle config
        vehicle = await VehicleService.get_vehicle(vehicle_id)
        if not vehicle:
            yield {
                "status": "error",
                "progress": 0,
                "message": f"Vehicle {vehicle_id} not found",
                "total_ids": 0,
                "fetched_ids": 0,
                "new_count": 0,
            }
            return

        year = vehicle.get("year", "")
        model = vehicle.get("model", "")
        keywords = vehicle.get("keywords", [])

        yield {
            "status": "fetching",
            "progress": 5,
            "message": "Fetching vehicle details from NHTSA...",
            "total_ids": 0,
            "fetched_ids": 0,
            "new_count": 0,
        }

        # Step 1: Get vehicle details and extract IDs
        try:
            details = await client.get_vehicle_details(vehicle_id)
        except Exception as e:
            yield {
                "status": "error",
                "progress": 5,
                "message": f"Failed to fetch vehicle details: {e}",
                "total_ids": 0,
                "fetched_ids": 0,
                "new_count": 0,
            }
            return

        nhtsa_ids = extract_comm_ids_from_details(details)
        id_to_summary = extract_id_to_summary(details)
        id_to_comm_number = extract_id_to_comm_number(details)

        if not nhtsa_ids:
            yield {
                "status": "complete",
                "progress": 100,
                "message": "No manufacturer communications found",
                "total_ids": 0,
                "fetched_ids": 0,
                "new_count": 0,
            }
            return

        total_ids = len(nhtsa_ids)
        yield {
            "status": "fetching",
            "progress": 10,
            "message": f"Found {total_ids} communication IDs. Checking cache...",
            "total_ids": total_ids,
            "fetched_ids": 0,
            "new_count": 0,
        }

        # Check which IDs we need to fetch
        if not force_refresh:
            existing = await db.communications.distinct(
                "nhtsa_id", {"nhtsa_id": {"$in": nhtsa_ids}}
            )
            ids_to_fetch = [nid for nid in nhtsa_ids if nid not in existing]
        else:
            ids_to_fetch = nhtsa_ids

        cached_count = total_ids - len(ids_to_fetch)
        if cached_count > 0:
            yield {
                "status": "fetching",
                "progress": 15,
                "message": f"{cached_count} already cached. Fetching {len(ids_to_fetch)} new...",
                "total_ids": total_ids,
                "fetched_ids": cached_count,
                "new_count": 0,
            }

        # Step 2: Fetch missing communications
        new_count = 0
        fetched_count = cached_count

        async for nhtsa_id, comm_data in client.fetch_communications_batch(ids_to_fetch):
            fetched_count += 1
            progress = 15 + int((fetched_count / total_ids) * 80)

            if comm_data:
                # Process and store
                products = [
                    {
                        "product_year": str(p.get("productYear", "")),
                        "product_model": str(p.get("productModel", "")),
                        "product_make": p.get("productMake"),
                    }
                    for p in (comm_data.get("associatedProducts") or [])
                ]

                documents = [
                    {
                        "url": d.get("url", ""),
                        "summary": d.get("summary", "Unknown"),
                        "load_date": str(d.get("loadDate", "")),
                    }
                    for d in (comm_data.get("associatedDocuments") or [])
                ]

                summary = str(comm_data.get("summary", "") or "")
                matched_kw = CommunicationService._matches_keywords(summary, keywords)

                # Prefer communication number from vehicle details (has proper PIT/PIC/PIP prefix)
                # Fallback to the one from safety issues API
                comm_number = id_to_comm_number.get(nhtsa_id) or comm_data.get("manufacturerCommunicationNumber")
                doc = {
                    "nhtsa_id": nhtsa_id,
                    "vehicle_id": vehicle_id,
                    "communication_number": comm_number,
                    "communication_type": get_comm_type(comm_number, summary),
                    "communication_date": comm_data.get("communicationDate"),
                    "summary": summary,
                    "details_summary": id_to_summary.get(nhtsa_id),
                    "associated_products": products,
                    "associated_documents": documents,
                    "matched_keywords": matched_kw,
                    "fetched_at": datetime.now(timezone.utc),
                }

                # Upsert the communication
                await db.communications.update_one(
                    {"nhtsa_id": nhtsa_id},
                    {"$set": doc},
                    upsert=True,
                )
                new_count += 1

            yield {
                "status": "fetching",
                "progress": progress,
                "message": f"Fetched {fetched_count}/{total_ids} communications...",
                "total_ids": total_ids,
                "fetched_ids": fetched_count,
                "new_count": new_count,
            }

        # Step 3: Count matches
        match_query = {"vehicle_id": vehicle_id}
        if year:
            match_query["associated_products.product_year"] = year
        if model:
            match_query["associated_products.product_model"] = {"$regex": model, "$options": "i"}

        matched_count = await db.communications.count_documents(match_query)

        # Update vehicle stats
        await VehicleService.update_fetch_stats(vehicle_id, matched_count)

        duration = time.time() - start_time
        yield {
            "status": "complete",
            "progress": 100,
            "message": f"Complete! Found {matched_count} matching communications in {duration:.1f}s",
            "total_ids": total_ids,
            "fetched_ids": total_ids,
            "new_count": new_count,
        }

    @staticmethod
    async def delete_communications(vehicle_id: int) -> int:
        """Delete all communications for a vehicle."""
        db = get_database()
        result = await db.communications.delete_many({"vehicle_id": vehicle_id})
        return result.deleted_count

    @staticmethod
    async def get_vehicle_stats(vehicle_id: int) -> dict[str, Any]:
        """Get statistics for a vehicle's communications."""
        db = get_database()

        # Total count
        total_count = await db.communications.count_documents({"vehicle_id": vehicle_id})

        # Last 30 days count
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        # Format as ISO with Z suffix to match stored format
        thirty_days_str = thirty_days_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
        last_30_query = {
            "vehicle_id": vehicle_id,
            "communication_date": {"$gte": thirty_days_str},
        }
        last_30_count = await db.communications.count_documents(last_30_query)

        # Category breakdown using aggregation
        pipeline = [
            {"$match": {"vehicle_id": vehicle_id}},
            {"$group": {"_id": "$communication_type", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        category_cursor = db.communications.aggregate(pipeline)
        categories = []
        async for doc in category_cursor:
            type_code = doc["_id"] or "OTHER"
            categories.append({
                "type": type_code,
                "label": COMM_TYPE_MAP.get(type_code, "Other"),
                "count": doc["count"],
            })

        return {
            "vehicle_id": vehicle_id,
            "total_count": total_count,
            "last_30_days_count": last_30_count,
            "categories": categories,
        }
