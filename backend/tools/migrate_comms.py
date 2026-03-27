"""
One-off migration: backfill communication dates and deduplicate per-vehicle ownership.

1. Parses string `communication_date` fields into proper datetime objects.
2. Duplicates communications that belong to multiple vehicles (compound key migration).
3. Drops the old unique index on `nhtsa_id` alone, creates compound unique on (nhtsa_id, vehicle_id).

Safe to re-run — skips already-migrated docs.

Usage:
    cd backend && source venv/bin/activate
    python -m tools.migrate_comms
"""

import asyncio
from datetime import datetime

from dateutil import parser as dateutil_parser
from motor.motor_asyncio import AsyncIOMotorClient

from src.config import get_settings


async def migrate():
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_url)
    db = client[settings.mongodb_database]

    comms = db.communications
    vehicles = db.vehicles

    # --- Step 1: Fix communication_date strings → datetime ---
    print("Step 1: Parsing communication_date strings to datetime...")
    date_fixed = 0
    date_skipped = 0
    date_failed = 0

    cursor = comms.find({"communication_date": {"$type": "string"}})
    async for doc in cursor:
        raw = doc.get("communication_date")
        if not raw:
            date_skipped += 1
            continue
        try:
            parsed = dateutil_parser.parse(str(raw))
            await comms.update_one({"_id": doc["_id"]}, {"$set": {"communication_date": parsed}})
            date_fixed += 1
        except (ValueError, TypeError):
            date_failed += 1

    print(f"  Dates fixed: {date_fixed}, skipped: {date_skipped}, failed: {date_failed}")

    # --- Step 2: Duplicate shared communications per vehicle ---
    print("Step 2: Ensuring each vehicle has its own communication copies...")

    # Get all vehicle_ids we're tracking
    all_vehicles = {}
    async for v in vehicles.find({}, {"vehicle_id": 1, "year": 1, "model": 1}):
        all_vehicles[v["vehicle_id"]] = v

    duplicated = 0
    already_ok = 0

    # For each communication, check if its associated_products overlap with other tracked vehicles
    all_comms = await comms.find({}).to_list(length=None)
    seen_pairs = set()

    for doc in all_comms:
        nhtsa_id = doc["nhtsa_id"]
        owner_vid = doc["vehicle_id"]
        seen_pairs.add((nhtsa_id, owner_vid))

    for doc in all_comms:
        nhtsa_id = doc["nhtsa_id"]
        owner_vid = doc["vehicle_id"]
        products = doc.get("associated_products") or []

        # Check if any other tracked vehicle matches this communication's products
        for vid, vehicle in all_vehicles.items():
            if vid == owner_vid:
                continue
            if (nhtsa_id, vid) in seen_pairs:
                already_ok += 1
                continue

            # Check product match
            v_year = str(vehicle.get("year", ""))
            v_model = str(vehicle.get("model", "")).upper()

            for p in products:
                p_year = str(p.get("product_year", "")).strip()
                p_model = str(p.get("product_model", "")).strip().upper()
                if p_year == v_year and p_model == v_model:
                    # This vehicle should have a copy
                    new_doc = {k: v for k, v in doc.items() if k != "_id"}
                    new_doc["vehicle_id"] = vid
                    try:
                        await comms.insert_one(new_doc)
                        duplicated += 1
                        seen_pairs.add((nhtsa_id, vid))
                    except Exception:
                        # Already exists (race or re-run)
                        pass
                    break

    print(f"  Duplicated: {duplicated}, already correct: {already_ok}")

    # --- Step 3: Update indexes ---
    print("Step 3: Updating indexes...")

    # Drop old unique index on nhtsa_id alone
    existing_indexes = await comms.index_information()
    for name, info in existing_indexes.items():
        keys = [k for k, _ in info["key"]]
        if keys == ["nhtsa_id"] and info.get("unique"):
            print(f"  Dropping old unique index: {name}")
            await comms.drop_index(name)
            break

    # Create compound unique index
    await comms.create_index(
        [("nhtsa_id", 1), ("vehicle_id", 1)],
        unique=True,
        name="nhtsa_id_vehicle_id_unique",
    )
    print("  Created compound unique index on (nhtsa_id, vehicle_id)")

    # Ensure vehicle_id index still exists
    await comms.create_index("vehicle_id")
    await comms.create_index("communication_date")

    # --- Step 4: Recount vehicle stats ---
    print("Step 4: Recounting vehicle comm_count stats...")
    recounted = 0
    async for v in vehicles.find({}, {"vehicle_id": 1}):
        vid = v["vehicle_id"]
        count = await comms.count_documents({"vehicle_id": vid})
        await vehicles.update_one(
            {"vehicle_id": vid},
            {"$set": {"comm_count": count, "updated_at": datetime.utcnow()}},
        )
        recounted += 1
        print(f"  Vehicle {vid}: {count} communications")

    print(f"  Recounted {recounted} vehicles")

    client.close()
    print("\nMigration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
