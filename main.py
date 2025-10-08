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
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)


def daily_details_cache_path() -> str:
    today = datetime.now().strftime("%Y%m%d")
    return os.path.join(CACHE_DIR, f"details_{today}.pkl")


def safety_db_cache_path() -> str:
    return os.path.join(CACHE_DIR, "safety_issues.pkl")


def load_pickle(path: str) -> Any:
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        return None


def save_pickle(path: str, obj: Any) -> None:
    # Write atomically when possible
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(obj, f)
    os.replace(tmp_path, path)


def fetch_details_with_cache(session: requests.Session) -> Dict[str, Any]:
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
    ensure_cache_dir()
    path = safety_db_cache_path()
    db = load_pickle(path)
    if isinstance(db, dict):
        return db
    return {}


def save_safety_db(db: Dict[str, Any]) -> None:
    save_pickle(safety_db_cache_path(), db)


def extract_nhtsa_ids_from_details(details: Dict[str, Any]) -> List[int]:
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
    products = comm.get("associatedProducts") or []
    for p in products:
        year = str(p.get("productYear", "")).strip()
        model = str(p.get("productModel", "")).strip()
        if year == TARGET_YEAR and model.upper() == TARGET_MODEL.upper():
            return True
    return False


def extract_document_urls(comm: Dict[str, Any]) -> List[str]:
    urls = []
    docs = comm.get("associatedDocuments") or []
    for d in docs:
        url = d.get("url")
        if isinstance(url, str) and url:
            urls.append(url)
    return urls


def main() -> None:
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

        # 6) Filter for our target product
        matching = [c for c in comms if product_matches(c)]

        # 7) Extract associated document URLs
        url_results = []
        for c in matching:
            urls = extract_document_urls(c)
            if urls:
                url_results.extend(urls)

        if not url_results:
            print("No associated document URLs found for 2024 Silverado EV.")
        else:
            print("Associated document URLs for 2024 Silverado EV:")
            for u in url_results:
                print(u)

    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()