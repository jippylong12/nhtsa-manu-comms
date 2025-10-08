"""nhtsa-manu-comms

Thin CLI entry point that orchestrates fetching and filtering NHTSA manufacturer
communications using helpers split across dedicated modules.
"""

import requests

from http_utils import create_session
from comms import discover_manufacturer_comm_ids, fetch_communications_for_ids
from processing import (
    filter_update_related_comms,
    group_urls_by_summary,
    print_grouped_urls,
)
from config import TARGET_YEAR, TARGET_MODEL


def main() -> None:
    """Entry point: orchestrates fetching, caching, filtering, and printing results."""
    session = create_session()
    header_text = f"{TARGET_YEAR} {TARGET_MODEL} with update-related summaries"
    try:
        # 1) Discover IDs from vehicle details (daily-cached)
        nhtsa_ids = discover_manufacturer_comm_ids(session)
        if not nhtsa_ids:
            print("No manufacturer communications found in details response.")
            return

        # 2) Fetch communications for the discovered IDs (uses cache + parallelism)
        comms = fetch_communications_for_ids(session, nhtsa_ids)

        # 3) Filter for our target product and keywords
        matching = filter_update_related_comms(comms)

        # 4) Group associated document URLs by summary label and print
        url_results = group_urls_by_summary(matching)
        print_grouped_urls(url_results, header_text)

    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()