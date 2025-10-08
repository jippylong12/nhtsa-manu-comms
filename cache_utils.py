"""Caching and persistence utilities for NHTSA API responses."""
from datetime import datetime
from typing import Any, Dict
import os
import pickle
import requests

from config import CACHE_DIR, DETAILS_URL, DETAILS_PARAMS


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
