"""Service for fetching data from NHTSA API."""

import asyncio
from typing import Any, AsyncGenerator
import httpx

from src.config import get_settings


class NHTSAClient:
    """Async client for NHTSA API."""

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.nhtsa_api_base_url
        self.headers = {
            "User-Agent": "nhtsa-manu-comms/2.0 (+https://nhtsa.gov)",
            "Accept": "application/json",
        }

    async def get_vehicle_details(self, vehicle_id: int) -> dict[str, Any]:
        """Fetch vehicle details including manufacturer communications list."""
        url = f"{self.base_url}/vehicles/{vehicle_id}/details"
        params = {
            "data": "complaints,recalls,investigations,manufacturercommunications",
            "productDetail": "minimal",
            "name": "",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get_safety_issue(self, nhtsa_id: int, max_retries: int = 3) -> dict[str, Any] | None:
        """Fetch a single manufacturer communication by NHTSA ID with retry logic."""
        url = f"{self.base_url}/safetyIssues/byNhtsaId"
        params = {
            "offset": 0,
            "max": 20,
            "sort": "id",
            "filter": "issueType",
            "filterValue": "manufacturerCommunications",
            "nhtsaId": nhtsa_id,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(max_retries):
                try:
                    response = await client.get(url, params=params, headers=self.headers)
                    if response.status_code == 403:
                        # Rate limited - wait and retry with exponential backoff
                        if attempt < max_retries - 1:
                            wait_time = 2 ** attempt  # 1s, 2s, 4s
                            await asyncio.sleep(wait_time)
                            continue
                        return None
                    response.raise_for_status()
                    data = response.json()
                    return self._extract_communication(data, nhtsa_id)
                except httpx.RequestError:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    return None
        return None

    def _extract_communication(
        self, response: dict[str, Any], target_id: int
    ) -> dict[str, Any] | None:
        """Extract the matching communication from API response."""
        try:
            results = response.get("results") or []
            if not results:
                return None

            comms = results[0].get("manufacturerCommunications") or []
            for comm in comms:
                nhtsa_id = comm.get("nhtsaIdNumber")
                try:
                    if int(str(nhtsa_id)) == target_id:
                        return comm
                except (TypeError, ValueError):
                    continue

            # Fallback to first if target not found
            return comms[0] if comms else None
        except Exception:
            return None

    async def fetch_communications_batch(
        self,
        nhtsa_ids: list[int],
        max_concurrent: int = 3,  # Reduced from 5 to avoid rate limiting
    ) -> AsyncGenerator[tuple[int, dict[str, Any] | None], None]:
        """Fetch multiple communications with controlled concurrency."""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_with_semaphore(nhtsa_id: int) -> tuple[int, dict[str, Any] | None]:
            async with semaphore:
                # Small delay to prevent bursting
                await asyncio.sleep(0.2)
                result = await self.get_safety_issue(nhtsa_id)
                return nhtsa_id, result

        tasks = [fetch_with_semaphore(nid) for nid in nhtsa_ids]

        for coro in asyncio.as_completed(tasks):
            yield await coro

    async def get_model_years(self) -> list[int]:
        """Fetch available model years from Safety Ratings API."""
        # SafetyRatings is still fine for year discovery
        url = f"{self.base_url}/SafetyRatings"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                results = data.get("Results", [])
                years = []
                for r in results:
                    try:
                        years.append(int(r["ModelYear"]))
                    except (ValueError, KeyError, TypeError):
                        continue
                return sorted(years, reverse=True)
            except httpx.RequestError:
                return []

    async def get_makes_for_year(self, year: int) -> list[str]:
        """Fetch makes for a given model year using /vehicles/makes endpoint."""
        url = f"{self.base_url}/vehicles/makes"
        params = {"modelYear": year}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                return sorted(set(r["make"] for r in results if r.get("make")))
            except httpx.RequestError:
                return []

    async def get_models_for_make_year(self, year: int, make: str) -> list[str]:
        """Fetch models for a given year and make using /vehicles/models endpoint."""
        url = f"{self.base_url}/vehicles/models"
        params = {"modelYear": year, "make": make}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                return sorted(set(r["vehicleModel"] for r in results if r.get("vehicleModel")))
            except httpx.RequestError:
                return []

    async def get_trims_for_model(self, year: int, make: str, model: str) -> list[str]:
        """Fetch available trims for a given Y/M/M using /vehicles/trims endpoint."""
        url = f"{self.base_url}/vehicles/trims"
        params = {"modelYear": year, "make": make, "model": model}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                return sorted(set(r["trim"] for r in results if r.get("trim")))
            except httpx.RequestError:
                return []

    async def get_vehicle_variants(self, year: int, make: str, model: str, trim: str | None = None) -> list[dict[str, Any]]:
        """Fetch specific vehicle variants with vehicleId using /vehicles/byYmmt endpoint.
        
        This is the KEY endpoint that returns the correct vehicleId for use with
        /vehicles/{vehicleId}/details for manufacturer communications.
        """
        url = f"{self.base_url}/vehicles/byYmmt"
        params: dict[str, Any] = {"modelYear": year, "make": make, "model": model}
        if trim:
            params["trim"] = trim
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                # Return structured variant info with the correct vehicleId
                variants = []
                for r in results:
                    variants.append({
                        "vehicleId": r.get("vehicleId"),  # This is the CORRECT ID!
                        "ncapId": r.get("ncapId"),  # SafetyRatings ID (for reference)
                        "modelYear": r.get("modelYear"),
                        "make": r.get("make"),
                        "model": r.get("vehicleModel"),
                        "trim": r.get("trim"),
                        "series": r.get("series"),
                        "vehicleDescription": f"{r.get('vehicleModel', '')} {r.get('trim', '')} {r.get('series', '')}".strip(),
                    })
                return variants
            except httpx.RequestError:
                return []


def extract_comm_ids_from_details(details: dict[str, Any]) -> list[int]:
    """Extract manufacturer communication NHTSA IDs from vehicle details."""
    try:
        comms = details["results"][0]["safetyIssues"]["manufacturerCommunications"]
    except (KeyError, IndexError, TypeError):
        return []

    # Sort by date (keep all types now)
    all_comms = comms or []
    all_comms.sort(key=lambda x: x.get("communicationDate") or "", reverse=True)

    ids = []
    for c in all_comms:
        n = c.get("nhtsaIdNumber")
        try:
            ids.append(int(str(n)))
        except (TypeError, ValueError):
            continue
    return ids


def extract_id_to_summary(details: dict[str, Any]) -> dict[int, str]:
    """Extract NHTSA ID to summary mapping from vehicle details."""
    mapping: dict[int, str] = {}
    try:
        comms = details["results"][0]["safetyIssues"]["manufacturerCommunications"]
    except (KeyError, IndexError, TypeError):
        return mapping

    for c in (comms or []):
        n = c.get("nhtsaIdNumber")
        summary = str(c.get("summary", "") or "")
        try:
            mapping[int(str(n))] = summary
        except (TypeError, ValueError):
            continue
    return mapping


def extract_id_to_comm_number(details: dict[str, Any]) -> dict[int, str]:
    """Extract NHTSA ID to manufacturer communication number mapping."""
    mapping: dict[int, str] = {}
    try:
        comms = details["results"][0]["safetyIssues"]["manufacturerCommunications"]
    except (KeyError, IndexError, TypeError):
        return mapping

    for c in (comms or []):
        n = c.get("nhtsaIdNumber")
        comm_number = str(c.get("manufacturerCommunicationNumber", "") or "")
        try:
            if comm_number:
                mapping[int(str(n))] = comm_number
        except (TypeError, ValueError):
            continue
    return mapping

