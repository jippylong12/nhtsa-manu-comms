"""HTTP session utilities for NHTSA API access."""
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests


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
