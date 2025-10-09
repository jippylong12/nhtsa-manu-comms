"""Functions for discovering and fetching manufacturer communications."""
from typing import Any, Dict, List, Optional
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

from config import MAX_WORKERS, SAFETY_ISSUES_URL
from cache_utils import (
    fetch_details_with_cache,
    load_safety_db,
    save_safety_db,
)


def extract_nhtsa_ids_from_details(details: Dict[str, Any]) -> List[int]:
    """Extract a list of manufacturer communication NHTSA IDs from details JSON."""
    try:
        comms = details["results"][0]["safetyIssues"]["manufacturerCommunications"]
    except (KeyError, IndexError, TypeError):
        return []
    # Inline filter: skip entries whose manufacturerCommunicationNumber starts with "PI"
    # comms = [c for c in (comms or []) if not (isinstance(c.get("manufacturerCommunicationNumber"), str) and c["manufacturerCommunicationNumber"].startswith("PI"))]
    comms.sort(key=lambda x: x.get("communicationDate") or "", reverse=True)

    ids = []
    for c in comms or []:
        # search_term = "23-NA-151"
        # if search_term in c.get("manufacturerCommunicationNumber"):
        #     print(f"Matched manufacturerCommunicationNumber: {search_term}")
        n = c.get("nhtsaIdNumber")
        if isinstance(n, int):
            ids.append(n)
        else:
            try:
                ids.append(int(str(n)))
            except (TypeError, ValueError):
                continue
    return ids


def parse_manufacturer_communications_from_safety_resp(resp_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull and normalize the manufacturerCommunications list from safetyIssues API response."""
    try:
        results = resp_json.get("results") or []
        if not results:
            return []
        container = results[0]
        comms = container.get("manufacturerCommunications") or []
        for c in comms:
            if "nhtsaIdNumber" in c:
                try:
                    c["nhtsaIdNumber"] = int(str(c["nhtsaIdNumber"]))
                except (TypeError, ValueError):
                    pass
        return comms
    except Exception:
        return []


def fetch_safety_issue_by_id(
    nhtsa_id: int,
    session: requests.Session,
    db: Dict[str, Any],
    lock: threading.Lock,
) -> Optional[Dict[str, Any]]:
    """Fetch a single manufacturer communication by NHTSA ID with caching.

    Stores the result (or None on failure/403) in the safety DB to avoid
    repeated requests within or across runs.
    """
    key = str(nhtsa_id)
    cached = db.get(key)
    if cached is not None:
        return cached

    params = {
        "offset": 0,
        "max": 20,
        "sort": "id",
        "filter": "issueType",
        "filterValue": "manufacturerCommunications",
        "nhtsaId": nhtsa_id,
    }

    selected: Optional[Dict[str, Any]] = None
    try:
        resp = session.get(SAFETY_ISSUES_URL, params=params, timeout=30)
        if resp.status_code == 403:
            selected = None
        else:
            resp.raise_for_status()
            resp_json = resp.json()
            comms = parse_manufacturer_communications_from_safety_resp(resp_json)
            for c in comms:
                if int(c.get("nhtsaIdNumber", -1)) == int(nhtsa_id):
                    selected = c
                    break
            if selected is None and comms:
                selected = comms[0]
    except requests.RequestException:
        selected = None
    except Exception:
        selected = None

    with lock:
        db[key] = selected
        save_safety_db(db)
    return selected


def fetch_communications_for_ids(session: requests.Session, nhtsa_ids: List[int]) -> List[Dict[str, Any]]:
    """Load cache DB, fetch missing IDs in parallel, and return communications in original order."""
    safety_db = load_safety_db()
    lock = threading.Lock()

    to_fetch = [i for i in nhtsa_ids if str(i) not in safety_db]

    if to_fetch:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(fetch_safety_issue_by_id, i, session, safety_db, lock): i for i in to_fetch}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    _ = fut.result()
                except Exception as e:
                    with lock:
                        safety_db[str(i)] = None
                        save_safety_db(safety_db)
                    print(f"Failed to fetch safety issue for nhtsaId {i}: {e}")

    comms: List[Dict[str, Any]] = []
    for i in nhtsa_ids:
        c = safety_db.get(str(i))
        if isinstance(c, dict):
            comms.append(c)
    return comms


def discover_manufacturer_comm_ids(session: requests.Session) -> List[int]:
    """Fetch daily-cached vehicle details and extract manufacturer comm IDs."""
    details = fetch_details_with_cache(session)
    return extract_nhtsa_ids_from_details(details)
