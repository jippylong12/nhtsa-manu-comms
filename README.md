# nhtsa-manu-comms

Fetch and filter NHTSA manufacturer communications for a specific vehicle, with simple on-disk caching and resilient HTTP requests.

This small utility script:
- Calls the NHTSA Vehicles API to discover manufacturer communication IDs for a given vehicle.
- Fetches full communication details from the NHTSA Safety Issues API (with retry-friendly HTTP and local caching).
- Filters communications by model year, model name, and keywords.
- Prints grouped document URLs for the matching communications.

> Current local date/time when this README was created: 2025-10-08 17:29

## Requirements
- Python 3.8+
- Internet access to reach api.nhtsa.gov
- Dependencies:
  - requests (automatically brings urllib3)

Install dependencies (recommended in a virtual environment):

```bash
pip install requests
```

## Quick start
1. Clone or open the project folder.
2. Inspect and (optionally) adjust configuration constants at the top of `main.py` (see Configuration below).
3. Run the script:

```bash
python main.py
```

## Configuration
Edit the constants near the top of `main.py` to suit your needs:

- VEHICLE_ID: NHTSA "vehicle ID" used by the details endpoint.
- TARGET_YEAR: Only communications matching this model year will be considered.
- TARGET_MODEL: Only communications for this model (case-insensitive) will be considered.
- KEYWORDS: A tuple of words searched for in the communication summary. Matching is done on whole words only.
- MAX_WORKERS: Maximum number of parallel requests when fetching safety issues by ID.
- CACHE_DIR: Directory used to persist cache files.

The script uses two NHTSA endpoints:
- Details: `https://api.nhtsa.gov/vehicles/{VEHICLE_ID}/details` – called once per day (response cached).
- Safety Issues by NHTSA ID: `https://api.nhtsa.gov/safetyIssues/byNhtsaId` – called for each ID that is not yet cached.

## Caching
Caching is enabled by default to reduce repeated requests:
- Daily details cache: `.cache/details_YYYYMMDD.pkl`
- Per-issue cache: `.cache/safety_issues.pkl` (a dictionary keyed by stringified nhtsaId)

The caches are safe to delete; the script will re-create them as needed.

## Output
If matching communications are found, the script prints grouped document URLs like:

```
Associated document URLs for 2024 Silverado EV with update-related summaries:

=== SOME SUMMARY TITLE ===
  https://.../doc1.pdf
  https://.../doc2.pdf
```

Otherwise it prints a helpful message indicating nothing was found.

## Notes on filtering
- Product filter matches exact year and case-insensitive model string.
- Keyword filter looks at the communication summary and compares whole words (lowercased) to the configured KEYWORDS. If KEYWORDS is empty, nothing matches by design.

## Troubleshooting
- Receiving 403s: The script is configured with polite headers and a retry strategy. Temporary 403s are handled gracefully and cached as missing for that ID for the session. You can delete `.cache/safety_issues.pkl` to force re-fetching.
- No results: Ensure your `TARGET_YEAR`, `TARGET_MODEL`, and `KEYWORDS` match what appears in the NHTSA data for your vehicle.
- Networking errors: They’re caught and printed; try re-running later.

## Project structure
- `main.py` – the script with clear function-level docstrings and inline comments.
- `README.md` – this documentation.
- `.cache/` – created on first run; contains pickle files for caching.

## License
No license specified. Add one if you intend to distribute or open-source.
