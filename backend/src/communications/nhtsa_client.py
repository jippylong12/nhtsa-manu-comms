"""Client for NHTSA's per-communication detail endpoint.

Reduced to a single job since the plain-nameplate re-key (ADR 0001, M3): given a
NHTSA ID, fetch that one manufacturer communication (its summary, documents, and
metadata) from `/safetyIssues/byNhtsaId`. Which ids belong to a vehicle now comes
from each vehicle's scanned `vehicle_bulletins` (see `src/communications/flatfile.py`),
not from this client.

Everything that walked NHTSA's crash-test taxonomy is gone: `get_vehicle_details`,
`get_vehicle_variants` (byYmmt), the `/vehicles/*` discovery calls, and the
`extract_*_from_details` helpers. They resolved a numeric `vehicleId` through a
route NHTSA's gateway hard-killed at 30s; the flat-file index removes that whole
failure mode. What survives is the byNhtsaId fetch, whose retry/backoff is
unchanged.
"""

import asyncio
from typing import Any, AsyncGenerator

import httpx

from src.config import get_settings


class NHTSAClient:
    """Async client for NHTSA's per-communication detail endpoint."""

    def __init__(self):
        settings = get_settings()
        self.base_url = settings.nhtsa_api_base_url
        self.headers = {
            "User-Agent": "nhtsa-manu-comms/2.0 (+https://nhtsa.gov)",
            "Accept": "application/json",
        }

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
                    # 403 is NHTSA's rate limit; 5xx is its gateway/backend trouble
                    # (the byNhtsaId endpoint 504s in unhealthy windows). Both are
                    # transient and pass on a later attempt, so retry with backoff
                    # and, if they persist, return None -- a per-communication miss
                    # the caller counts as a fetch failure. This is now the only
                    # NHTSA call in the fetch path, so it must degrade rather than
                    # raise: a single 504 must not abort a whole vehicle's sync or
                    # the run. A 4xx other than 403 will not pass and still surfaces.
                    if response.status_code == 403 or response.status_code >= 500:
                        if attempt < max_retries - 1:
                            wait_time = 2**attempt  # 1s, 2s, 4s
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
