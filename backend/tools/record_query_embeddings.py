"""Record the query embeddings the test suite replays, so it never calls Gemini.

The suite used to embed its search queries for real on every run: billed spend
on each `pytest`, and an unbounded network call in the middle of a test. This
records those vectors once, into a fixture the suite reads instead.

What is stored is exactly what `embed_query` returns — the same post-truncation
normalised vector — so retrieval, the cosine numbers, and the confidence gate
behave in tests precisely as they do in production. Values are rounded to 8
decimals because `to_pgvector` formats with `%.8f`: anything finer never
reaches Postgres, so the rounding is invisible by construction.

Re-record when a test's query text changes (the suite fails loudly and names
this script when it finds no recording for a query):

    cd backend && python3 -m tools.record_query_embeddings
"""

import asyncio
import json
import pathlib
import sys

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "query_embeddings.json"
)

# Every query the suite embeds. Keep in sync with the test modules that call
# `service.search()` or `retrieve()` without supplying their own vector.
QUERIES = [
    # tests/test_usage_ledger.py — both end-to-end ledger tests.
    "the tailgate will not open",
]


async def main() -> int:
    from src.config import get_settings
    from src.jobs.embed import embed_query

    if not get_settings().gemini_api_key:
        print("GEMINI_API_KEY is not set; cannot record.", file=sys.stderr)
        return 1

    # QUERIES is the only source of truth, deliberately: the file is rewritten
    # wholesale, so accepting ad-hoc queries on argv would mean the next plain
    # run silently dropped them — and the test relying on that recording would
    # start failing with no hint as to why. Adding a query is an edit here.
    recorded = {}
    for q in sorted(set(QUERIES)):
        vector = await embed_query(q)
        recorded[q] = [round(v, 8) for v in vector]
        print(f"recorded {len(vector)}d :: {q!r}")

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(recorded, indent=1, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE.relative_to(FIXTURE.parents[2])} ({FIXTURE.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
