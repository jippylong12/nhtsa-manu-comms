# Symptom Search — Eval & Quality Report (Phase B)

Measured quality for the symptom-search retrieval + grounded-answer pipeline
(milestones B1/B2). Reproduce with:

```bash
cd backend
python -m src.eval.run_eval          # retrieval only (no LLM): hit@k, MRR, embed cost
python -m src.eval.quality_eval      # full pipeline: grounding, refusal, hallucination, cost
python -m src.eval.quality_eval --hardest --model gemini-3-flash-preview   # model comparison
```

The golden set (`backend/src/eval/golden_set.json`) is 39 queries: 29 real
symptom queries (each hand-verified to a communication that exists for that
vehicle) and 10 adversarial no-match queries (systems this EV corpus cannot
contain — diesel, carburetor, clutch, spark plugs, plus colloquial
"death wobble" and a "gas mileage / fuel gauge" trap that could false-match the
TOP TIER fuel bulletin). Queries are phrased in owner/technician language,
deliberately paraphrased away from the documents' own tag words so retrieval is
exercised rather than echoed.

## Retrieval tuning (JIP-381)

Fusion blends pgvector cosine similarity and Postgres full-text search by
Reciprocal Rank Fusion, with a small tag boost when the query names a known
system. A parameter sweep (vector/FTS weights, RRF damping, tag boost) over the
29 match queries produced:

| config | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| baseline `W_vec=1.0, W_fts=1.0, tag=0.004, k=60` | 58.6% | 65.5% | 82.8% | 0.674 |
| **tuned `W_vec=2.0, W_fts=0.5, tag=0.001, k=60`** | **62.1%** | **86.2%** | **93.1%** | **0.739** |

**Finding.** For paraphrased symptom queries the semantic (vector) signal is far
stronger than lexical FTS; weighting vector 4:1 over FTS is the dominant lever
(hit@3 +20.7pts, hit@5 +10.3pts). The tag boost tested neutral-to-negative at
these weights — values >= 0.002 lowered hit@3 by promoting broad multi-system
OTA bulletins that share a system tag but are not the specific match — so it is
kept at 0.001 as a tiebreaker, not disabled (the mechanism still helps when a
query names a system explicitly).

## Full-pipeline quality (tuned, `gemini-3.1-flash-lite`)

`python -m src.eval.quality_eval` over all 39 queries:

| metric | value | bar |
|---|---|---|
| grounded answers (match queries) | 29/29 (100%) | — |
| hallucinated citations | **0** | must be 0 |
| false refusals (match queries refused) | 0 | ~0 |
| hit@1 / hit@3 / hit@5 | 62.1% / 86.2% / 93.1% | — |
| **no-match refused** | **10/10 (100%)** | must be 100% |
| false answers on no-match | **0** | must be 0 |
| avg cost / query | $0.000302 | << $0.01 |
| max cost / query | $0.000550 | << $0.01 |

Refusal precision is perfect, including the two hardest adversarial cases:
`nm_death_wobble` (colloquial front-end oscillation) and `nm_gas_mileage` (which
was designed to bait the TOP TIER fuel bulletin). Both refused, at $0 LLM cost —
the confidence gate (top-k cosine < 0.65) short-circuits before the model call.

## Model decision — Flash-Lite vs Flash

`--hardest` subset (8 match + 2 no-match), same retrieval, composition model varied:

| model | grounded | hallucinated | refused | avg $/query |
|---|---|---|---|---|
| **gemini-3.1-flash-lite** (chosen) | 8/8 | 0 | 2/2 | **$0.000342** |
| gemini-3-flash-preview | 8/8 | 0 | 2/2 | $0.000507 (+48%) |

Example (query: "service high voltage warning is on and the cabin blows no heat"):

- Flash-Lite ($0.000421): "Multiple communications address the 'Service high
  voltage' message combined with a loss of cabin heat. Bulletins [11014617] and
  [11017177] indicate that a software update can resolve these issues, while
  [11013402] and [11012957] note that a software update may be under development."
- Flash ($0.000792): a longer answer citing the same core bulletins plus extra
  mechanism detail.

**Verdict: keep `gemini-3.1-flash-lite`.** Grounding, citations, and refusal
behaviour are identical; Flash is ~48% more expensive and its extra verbosity is
not an improvement for a short consumer-facing answer. Escalation criteria if
this changes: hallucinated citations > 0, or grounded rate materially below
Flash on a future golden-set expansion.

## Confidence-gate recalibration (post-B3 dogfooding)

The refusal gate was initially set at cosine **0.65**, derived from golden queries
scoring >= 0.668 against absurd controls scoring <= 0.624. Real use exposed that
as over-fitted: the golden queries were authored *from* the documents, so they
scored unusually high, while real user queries are shorter and vaguer and land at
**0.62-0.72** — straddling the threshold. On the 653-communication 2024 Silverado,
legitimate searches were silently refused:

| query | top-k cosine | at 0.65 | at 0.55 |
|---|---|---|---|
| `battery` | 0.646 | refused | answered (5 cited bulletins) |
| `screen` | 0.622 | refused | answered |
| `check engine light` | 0.637 | refused | answered |
| `brakes` | 0.650 | answered (borderline) | answered |

**Fix: threshold lowered to 0.55.** The cosine gate is only a cheap pre-filter to
avoid LLM spend on obvious garbage; correctness is owned by the model's own
relevance judgment plus the citation guard. Re-running the full 39-query eval
after the change confirmed the safety properties are unchanged: **10/10 no-match
queries still refused, 0 false answers, 0 hallucinated citations**, grounded
29/29, hit@k identical. Cost rose only from $0.000302 to $0.000356 per query,
since absurd queries now reach the model instead of being cut for free.

**Lesson:** a golden set authored from the corpus measures retrieval well but
over-estimates real-world query confidence. Calibrate thresholds against queries
written *without* looking at the documents.

## Known failure modes

- **Near-duplicate infotainment bulletins.** `ipc_radio_blank_after_update`
  (PIT6018F) is the one retrieval miss: the corpus has many "blank screen / no
  audio after update" bulletins and the specific revision ranks outside top-10.
  The composed answer still cites genuinely relevant blank-screen bulletins, so
  it is a ranking miss, not an ungrounded answer.
- **Colloquial feature names.** `handsfree_cruise_flaky_colloquial` ("hands-free
  highway cruise") retrieves other Super Cruise bulletins ahead of the exact
  golden one. Grounded, but the target lands at rank > 5.
- **Multi-revision bulletin families** — *fixed after dogfooding.* Manufacturers
  republish a bulletin under the same number with a revision suffix (PIT6202 ->
  PIT6202A -> ... -> PIT6202D), and each revision is its own NHTSA record. 406 of
  the 805 numbered communications in this corpus are such revisions, so a search
  for "hood ajar" returned the same bulletin four times. Retrieval now collapses
  a family to its current revision (newest publication date), inherits the best
  score any revision achieved, and reports `supersededCount` so the UI can show
  "latest of 5 revisions". The stem rule is deliberately conservative — a
  trailing letter only counts after a digit, a trailing `-NN` only on a long
  campaign id — so 24-NA-125, 653.30, and N242484500 are left intact.
- **Citation set varies run to run.** The model is temperature 0 but not
  perfectly deterministic; which grounded documents it cites can shift slightly
  between runs. Every cited id is still guaranteed to be in the retrieved set by
  the post-generation citation guard, so this never produces a hallucination.

## Quality bar

The agreed bar (hit@5 >= 80%, zero hallucinated citations, no false answers on
no-match) is met with margin: hit@5 93.1%, 0 hallucinations, 10/10 no-match
refused. No re-negotiation needed before B3 UI work.
