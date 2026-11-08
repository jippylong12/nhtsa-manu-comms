# Cost Ledger

Every billable model call in this project writes one row to `usage_events`. That
table is the answer to "what has this cost me", both for the corpus pipeline and
for query-time symptom search.

## Why two tables

`pipeline_runs` and `usage_events` are kept separate on purpose:

- **`pipeline_runs`** is the *operational* record of a job execution: did the
  nightly run happen, did it succeed, what did it count, what error did it hit.
- **`usage_events`** is the *billing* record: one row per billable call. Query
  time spend happens outside any job, so it has no `pipeline_runs` row to live
  on, and a single job run can contain hundreds of separately-billed calls.

Historical token/cost totals already on `pipeline_runs` were backfilled into
`usage_events` by `migrations/0003_usage_events.sql`, so no spend history was
lost. Backfilled rows carry `meta->>'backfilled_from_pipeline_run'`.

## Event kinds

| kind | written by | when |
|---|---|---|
| `llm_extract` | `jobs/llm.py` | each document extraction (live and batch), model `claude-haiku-5-5` |
| `embed` | `jobs/embed.py` | each corpus embedding batch |
| `search_embed` | `symptom_search/service.py` | embedding one user query |
| `answer_compose` | `symptom_search/service.py` | the grounded-answer LLM call (`claude-haiku-5-5` since 2026-11-07; Gemini Flash-Lite before) |
| `extract`, `digest` | reserved / backfill | PDF text extraction, digest sends |

An `llm_extract` row carries two `meta` keys besides `project`: `path` (`live` or
`batch`) and `input` (`pdf`, `text` or `chunks`, the plan the document was read
under; a chunked document writes one row per call).

A gated refusal never calls the model, so it produces a `search_embed` row and
no `answer_compose` row. The two rows of one search share a `query_id`.

## Single source of truth for pricing

Rates live in `src/usage/pricing.py`. The jobs, the per-query cost shown in the
UI, and the ledger all compute cost from that table, so ledger totals reconcile
with the provider's billing and a price change is a one-line edit.

`record_usage()` in `src/usage/ledger.py` is the only way to write a row, and it
derives cost itself, so a caller cannot record a figure from a stale rate. A test
(`test_every_module_that_calls_the_model_sdk_records_usage`) fails if a new
module calls the model SDK without metering.

## Query it directly

**What has this cost me this month, in total:**

```sql
SELECT count(*)                AS events,
       sum(tokens_in)          AS tokens_in,
       sum(tokens_out)         AS tokens_out,
       round(sum(cost_usd), 6) AS cost_usd
FROM usage_events
WHERE occurred_at >= date_trunc('month', now());
```

**This month, broken down by what was paid for:**

```sql
SELECT kind,
       count(*)                AS events,
       round(sum(cost_usd), 6) AS cost_usd
FROM usage_events
WHERE occurred_at >= date_trunc('month', now())
GROUP BY kind
ORDER BY sum(cost_usd) DESC;
```

**Daily spend for the last 30 days:**

```sql
SELECT date_trunc('day', occurred_at)::date AS day,
       round(sum(cost_usd), 6)              AS cost_usd
FROM usage_events
WHERE occurred_at >= now() - interval '30 days'
GROUP BY 1 ORDER BY 1 DESC;
```

**What one symptom search cost (embedding + composition):**

```sql
SELECT query_id, kind, tokens_in, tokens_out, cost_usd
FROM usage_events
WHERE query_id IS NOT NULL
ORDER BY occurred_at DESC LIMIT 10;
```

**Average cost per symptom search:**

```sql
SELECT round(avg(q.cost), 8) AS avg_cost_per_search, count(*) AS searches
FROM (
    SELECT query_id, sum(cost_usd) AS cost
    FROM usage_events
    WHERE query_id IS NOT NULL
    GROUP BY query_id
) q;
```

## Read API

`GET /api/admin/usage` returns the same numbers as JSON: totals plus breakdowns
by `kind`, `model`, and day, with optional `date_from` / `date_to` bounds. The
breakdowns are computed from the same rows as the totals, so they reconcile by
construction (asserted in `tests/test_usage_ledger.py`).

```bash
curl 'http://localhost:8000/api/admin/usage'
curl 'http://localhost:8000/api/admin/usage?date_from=2026-07-01'
```

It is unauthenticated because the deployment is single-tenant, but it lives under
`/admin` so Phase C can lock the whole prefix down in one place. `usage_events`
also has a nullable `user_id` column reserved for that work, so per-account
attribution needs no migration.

## What the test suite spends

Running `pytest` used to bill a Gemini embedding on every run, because the two
end-to-end ledger tests call `service.search()`, which embeds the query for
real. Those embeddings are now **replayed from a recording** —
`tests/fixtures/query_embeddings.json`, served by the autouse
`_replay_query_embeddings` fixture in `tests/conftest.py`.

The recording is the vector Gemini actually returned, so retrieval, the cosine
scores, and the confidence gate in `compose()` behave exactly as in production:
the tailgate query still scores 0.72 and still reaches the composer. Nothing is
stubbed out of the ledger path — `search_embed` tokens come from
`QueryCost.record_embed()`, which counts characters in the query string and
never read the API response to begin with.

The fixture is autouse so a new call site cannot quietly reintroduce live
spend. An unrecorded query raises rather than reaching the network:

```bash
cd backend && python3 -m tools.record_query_embeddings
```

One real billed call remains, deliberately:
`test_a_symptom_search_records_its_spend` still makes a live Flash-Lite
composition, because it is the JIP-409 acceptance test that spend from a real
model call reaches the ledger. It costs roughly $0.00005 a run.

### Timeouts

`genai.Client()` defaults to **no timeout**, and a stalled TLS connection then
blocks forever rather than failing — one suite run hung 40+ minutes parked in
`_ssl__SSLSocket_read`, with no way to fail and nothing in the database to
explain it. Every client is now built by `src/gemini.py`, which applies
`GEMINI_TIMEOUT_SECONDS` (default 120, generous enough for a 50-document
embedding batch or a vision extraction). Construct clients through that helper
so a new call site cannot reintroduce the unbounded case by omission.

## Experiment spend

Spend on a one-off experiment is marked with `usage_events.meta->>'bakeoff'`, so it can be totalled apart from production. The extraction bake-off (`src/eval/extraction_bakeoff.py`, scored by `src/eval/bakeoff_score.py`) writes `meta.bakeoff` as the arm name (`G`, `H-text`, `H-pdf`, `H-text-live`, `H-pdf-live`, `S-text`, `S-pdf`, the three PDF arms `GL-pdf-high`, `GF-pdf-low`, `H-pdf-high`, and the Haiku effort arms `H-pdf-xhigh`, `H-pdf-max`) for each arm's responses and as `judge` (five-way) or `judge-pdf6` (six-way, reads the PDF) for the Opus judge's calls, with `meta.run` naming the run directory. Rows keep an ordinary `kind` (`llm_extract`), so the kind breakdown above still adds up; to total only the experiment, filter on the marker:

```sql
SELECT meta->>'bakeoff'        AS arm,
       count(*)                AS events,
       round(sum(cost_usd), 4) AS cost_usd
FROM usage_events
WHERE meta ? 'bakeoff'
GROUP BY ROLLUP (meta->>'bakeoff')
ORDER BY arm NULLS LAST;
```

The bake-off holds itself to one $30 cap (raised from $10 to $20 on 2026-11-06 for the Sonnet arms, then to $30 for the three PDF arms and the six-way judge) shared by every dollar it spends across every run directory under `backend/.bakeoff/`, so starting a new run never resets it. The cap check reads the files each piece writes, not this ledger (the ledger is per database): the arms' `results.jsonl`, every `judge.jsonl` (including `judge-pdf6/`), the pairwise judge's `*/verdicts.jsonl`, and the 2027 probe's `probe-*/results.json` (its `cost_true`, because the probe's own ledger rows were priced at the wrong rate). `submit` reuses the newest run unless `--new-run` is passed, so an arm submitted twice is refused. The judge refuses to start when the spend so far plus its estimate would pass the cap, and before every call it checks that the call's worst case (every `max_tokens` output token used) still fits, stopping early otherwise. Rows from the live arms (`run-live`, the synchronous Messages API at list price, `batch = false`) carry `meta.mode = "live"`, while the batch arms are costed at half price and carry no `mode`; `run-live` checks every call's worst case against the same cap before sending it.

`gemini-3.8-flash` (arm `GF-pdf-low`) has no row in `src/usage/pricing.py`, which would cost it at the flash-lite fallback rate. The bake-off harness registers its list price at import instead: $0.75 in / $3.75 out per 1M tokens (thinking is billed as output) through 2026-12-31, then $1.50 / $7.50, so the ledger rows of that arm carry the 2026 rate. Add the row to `pricing.py` before using the model anywhere else, and re-cost the arm at the 2027 rate for any comparison made after 2026-12-31.
