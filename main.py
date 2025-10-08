"""nhtsa-manu-comms

Fetch and filter NHTSA manufacturer communications for a specific vehicle, with
simple on-disk caching and resilient HTTP requests.

Workflow overview:
- Pull the vehicle details to discover manufacturer communication NHTSA IDs.
- For each ID, call the safety issues API (cached per ID) to get full details.
- Filter communications for the configured product (year/model) and keywords.
- Print grouped document URLs from matching communications.

This script is designed to be friendly to the NHTSA APIs by using a shared
requests.Session with retries and conservative headers. Caching reduces repeated
network calls across runs.
"""

import os
import pickle
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration
VEHICLE_ID = 218944
DETAILS_URL = f"https://api.nhtsa.gov/vehicles/{VEHICLE_ID}/details"
DETAILS_PARAMS = {
    "data": "complaints,recalls,investigations,manufacturercommunications",
    "productDetail": "minimal",
    "name": "",
}
SAFETY_ISSUES_URL = "https://api.nhtsa.gov/safetyIssues/byNhtsaId"
MAX_WORKERS = 5
CACHE_DIR = ".cache"

# Product filters
TARGET_YEAR = "2024"
TARGET_MODEL = "SILVERADO EV"  # will compare case-insensitively

# Keyword filters for summary field
KEYWORDS = (
    "sidewinder",
    # "software update",
    # "update",
    # "reprogram",
    # "reprogramming",
    # "calibration",
    # "flash",
)


def create_session() -> requests.Session:
    """Create a requests session with retries and polite headers to reduce 403s/blocks."""
    sess = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.6,
        status_forcelist=(403, 408, 429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update({
        "User-Agent": "nhtsa-manu-comms/1.0 (+https://nhtsa.gov)",
        "Accept": "application/json",
    })
    return sess


def ensure_cache_dir() -> None:
    """Ensure the on-disk cache directory exists."""
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)


def daily_details_cache_path() -> str:
    """Return the path to the daily details cache file (rotates by date)."""
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(CACHE_DIR, f"details_{today}.pkl")


def safety_db_cache_path() -> str:
    """Return the path to the persistent safety issues cache (by nhtsaId)."""
    return os.path.join(CACHE_DIR, "safety_issues.pkl")


def load_pickle(path: str) -> Any:
    """Load a Python object from a pickle file, returning None if missing."""
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        return None


def save_pickle(path: str, obj: Any) -> None:
    """Atomically save a Python object to a pickle file (write-then-replace)."""
    # Write atomically when possible
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(obj, f)
    os.replace(tmp_path, path)


def fetch_details_with_cache(session: requests.Session) -> Dict[str, Any]:
    """Fetch vehicle details once per day and cache the JSON on disk.

    The NHTSA details endpoint is used only to discover NHTSA IDs for
    manufacturer communications. Since this rarely changes within a day,
    we cache the raw response by date.
    """
    ensure_cache_dir()
    path = daily_details_cache_path()
    cached = load_pickle(path)
    if cached is not None:
        return cached

    resp = session.get(DETAILS_URL, params=DETAILS_PARAMS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    save_pickle(path, data)
    return data


def load_safety_db() -> Dict[str, Any]:
    """Load the per-nhtsaId safety issues cache dictionary from disk."""
    ensure_cache_dir()
    path = safety_db_cache_path()
    db = load_pickle(path)
    if isinstance(db, dict):
        return db
    return {}


def save_safety_db(db: Dict[str, Any]) -> None:
    """Persist the safety issues cache dictionary to disk."""
    save_pickle(safety_db_cache_path(), db)


def extract_nhtsa_ids_from_details(details: Dict[str, Any]) -> List[int]:
    """Extract a list of manufacturer communication NHTSA IDs from details JSON."""
    try:
        comms = details["results"][0]["safetyIssues"]["manufacturerCommunications"]
    except (KeyError, IndexError, TypeError):
        return []
    ids = []
    for c in comms or []:
        n = c.get("nhtsaIdNumber")
        if isinstance(n, int):
            ids.append(n)
        else:
            # sometimes numeric strings
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
        # normalize ints
        for c in comms:
            if "nhtsaIdNumber" in c:
                try:
                    c["nhtsaIdNumber"] = int(str(c["nhtsaIdNumber"]))
                except (TypeError, ValueError):
                    pass
        return comms
    except Exception:
        return []


def fetch_safety_issue_by_id(nhtsa_id: int, session: requests.Session, db: Dict[str, Any], lock: threading.Lock) -> Optional[Dict[str, Any]]:
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
        # Handle 403 gracefully to avoid noisy failures; retries are configured on the session
        if resp.status_code == 403:
            selected = None
        else:
            resp.raise_for_status()
            resp_json = resp.json()
            comms = parse_manufacturer_communications_from_safety_resp(resp_json)
            # Choose the communication matching this ID
            for c in comms:
                if int(c.get("nhtsaIdNumber", -1)) == int(nhtsa_id):
                    selected = c
                    break
            # If not found, keep the first as a fallback
            if selected is None and comms:
                selected = comms[0]
    except requests.RequestException:
        # Network/HTTP issues; cache as None to avoid spamming
        selected = None
    except Exception:
        selected = None

    # Store the result (possibly None) keyed by id to avoid repeat requests
    with lock:
        db[key] = selected
        save_safety_db(db)
    return selected


def product_matches(comm: Dict[str, Any]) -> bool:
    """Return True if the communication references the configured product.

    Matches by exact year and case-insensitive model string.
    """
    products = comm.get("associatedProducts") or []
    for p in products:
        year = str(p.get("productYear", "")).strip()
        model = str(p.get("productModel", "")).strip()
        if year == TARGET_YEAR and model.upper() == TARGET_MODEL.upper():
            return True
    return False


def is_update_related(summary: Optional[str]) -> bool:
    """Return True if the summary contains any configured KEYWORDS as whole words."""
    if not summary:
        return False
    words = {w.strip().lower() for w in str(summary).split() if w.strip()}
    keywords = {str(k).strip().lower() for k in KEYWORDS if isinstance(k, str) and k.strip()}
    if not keywords:
        return False
    return any(k in words for k in keywords)


def extract_document_urls(comm: Dict[str, Any]) -> Dict[str, List[str]]:
    """Extract document URLs grouped by summary type."""
    urls_by_summary = {}
    docs = comm.get("associatedDocuments") or []
    for d in docs:
        url = d.get("url")
        summary = d.get("summary", "Unknown")
        if isinstance(url, str) and url:
            if summary not in urls_by_summary:
                urls_by_summary[summary] = []
            urls_by_summary[summary].append(url)
    return urls_by_summary


def main() -> None:
    """Entry point: orchestrates fetching, caching, filtering, and printing results."""
    session = create_session()
    try:
        # 1) Details with daily cache
        details = fetch_details_with_cache(session)
        nhtsa_ids = extract_nhtsa_ids_from_details(details)
        if not nhtsa_ids:
            print("No manufacturer communications found in details response.")
            return

        # 2) Load safety issues DB
        safety_db = load_safety_db()
        lock = threading.Lock()

        # 3) Determine which IDs require fetching
        to_fetch = [i for i in nhtsa_ids if str(i) not in safety_db]

        # 4) Parallel fetch missing IDs (up to MAX_WORKERS at a time)
        if to_fetch:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = {ex.submit(fetch_safety_issue_by_id, i, session, safety_db, lock): i for i in to_fetch}
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        _ = fut.result()
                    except Exception as e:
                        # Record failure as None to avoid re-querying within this run
                        with lock:
                            safety_db[str(i)] = None
                            save_safety_db(safety_db)
                        print(f"Failed to fetch safety issue for nhtsaId {i}: {e}")

        # 5) Gather communications from DB in the original ID order
        comms: List[Dict[str, Any]] = []
        for i in nhtsa_ids:
            c = safety_db.get(str(i))
            if isinstance(c, dict):
                comms.append(c)

        # 6) Filter for our target product and update-related keywords in summary
        matching = [c for c in comms if product_matches(c) and is_update_related(c.get("summary"))]

        # 7) Extract associated document URLs grouped by summary type
        url_results = {}
        for c in matching:
            urls_by_summary = extract_document_urls(c)
            for summary, urls in urls_by_summary.items():
                if summary not in url_results:
                    url_results[summary] = []
                url_results[summary].extend(urls)

        if not url_results:
            print("No associated document URLs found for 2024 Silverado EV with update-related summaries.")
        else:
            print("Associated document URLs for 2024 Silverado EV with update-related summaries:\n")
            for summary, urls in url_results.items():
                print(f"=== {summary} ===")
                for u in urls:
                    print(f"  {u}")
                print()  # Empty line between sections

    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()