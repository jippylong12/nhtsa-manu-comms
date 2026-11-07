# ADR 0002 — Claude Haiku 5.5 reads each PDF for structured extraction

- **Status:** Implemented (schema-v2 project, Nov 2026)
- **Date:** 2026-11-07
- **Evidence:** the extraction bake-off (`src/eval/extraction_bakeoff.py`, scored by `src/eval/bakeoff_score.py`) over 300 real bulletins; Linear JIP-1566 to JIP-1569

## Context

Extraction used to run in two steps: PyMuPDF pulled the text locally, Gemini read that
text and filled the structured fields, and a separate vision pass handled documents
whose text failed the yield heuristic. The text-first design was chosen because sending
PDFs straight to Gemini cost about twice the input tokens and, on one table-dense
document, invented a model-year range for an empty cell.

Applicability (which vehicles a bulletin covers) is the field that matters most to a
technician and the one the text arms get wrong: tables flatten badly, and scanned PDFs
have no usable text at all.

The bake-off ran 300 real bulletins through Gemini 3.5 Flash-Lite, Claude Haiku 5.5 on
text, Haiku 5.5 reading the PDF (`H-pdf-live`) and Sonnet 5.5 (text and PDF), scored by
an Opus judge that reads the PDF.

| | Gemini | Haiku text | Haiku PDF | Sonnet PDF |
|---|---|---|---|---|
| Schema validity | 99.3% | 99.3% | 99.3% | 99.3% |
| Applicability recall | 0.63 | 0.64 | **0.68** | n/a |
| Judge mean rank | 4.33 | 3.35 | **2.93** | best overall |
| Cost per 1,000 documents | n/a | n/a | $1.49 live, $0.75 Batch | $13.61 Batch |

Sonnet PDF won the judge outright (43 of 60 head-to-head wins) at about 18 times the
corpus cost.

## Decision

**Claude Haiku 5.5, effort `high`, reads the PDF itself and returns the structured
fields through a JSON schema.** Embeddings (`gemini-embedding-001`) and the
symptom-search answer call (`settings.gemini_model`) stay on Gemini.

Effort `high` is the bake-off's `H-pdf-high` arm, which Marcus picked over `low`
(the first plan said `low`; that was a planning error). The request is that arm's:
`thinking: {"type": "adaptive"}` and `max_tokens` 16,000, because thinking counts
against `max_tokens`. The cost row above is `H-pdf-live` at low effort; the bake-off
measured high at about 18% more per document than low, so roughly $1.76 per 1,000
documents live and $0.89 on Batch.

### 1. Input plan per document

Documents over 100K input tokens bill at Haiku's long-context rate (probe on a 96-page
scan: $0.076 true against $0.015 at list), so long documents are never sent whole.
`jobs/pdf_input.py` chooses one plan per document:

- **pdf:** 30 pages or fewer and a file of 25 MB or less. The PDF itself, one call.
  30 pages at about 3,000 tokens each stays under the 100K threshold.
- **text:** longer or heavier, with usable local text (`extraction_method = 'pymupdf'`).
  The extracted text with the text prompt, one call (a 96-page text is about 37K tokens).
- **chunks:** longer or heavier, with garbled text (`vision-fallback`). Pages are
  rendered at 110 DPI JPEG into PDF chunks of at most 30 pages, one call per chunk,
  then one text call to merge. A payload still over 25 MB is re-rendered at 110 DPI.

### 2. Live and Batch

The in-app refresh reads live, because someone is waiting: the bake-off's Haiku
batches sat 25+ minutes, while 300 live documents took 3m17s at concurrency 6. `process
--all` and `run_pipeline` use the Batch API at half price; `--sync-llm` forces live.
The Batch path only sees pdf and text plans; chunked documents run live inside it.

### 3. The extract stage stays

Every document still goes through local extraction: the text feeds full-text search,
and the page count and the yield flag feed the planner. The LLM stage reads the PDF
from the local archive and re-downloads it when the archive has been pruned. The
LLM-stage vision prompt and pass are removed; `extraction_method = 'vision-fallback'`
survives only as a yield flag.

### 4. Failure handling

A schema-validation failure gets one retry. API errors get 4 tries with doubling
backoff. A 401, 403 or 404, or a credit-balance error, stops the run
(`ExtractionFatal`) and marks nothing failed.

### 5. Ledger

Each call records `llm_extract` for `claude-haiku-5-5` with `batch` set and
`meta = {path: live|batch, input: pdf|text|chunks, project: schema-v2}`.

## Consequences

- Better applicability recall and judge rank than the Gemini baseline, and scanned
  PDFs are read instead of skipped. Schema validity is unchanged.
- The whole 1,362-document corpus reprocesses for about $1 live.
- A second key: extraction needs `CLAUDE_API_KEY` (or `ANTHROPIC_API_KEY`);
  `GEMINI_API_KEY` is still needed for embeddings and answers.
- `comm_documents.llm_model` holds three values over time: `gemini-3.1-flash-lite` and
  `gemini-3.5-flash-lite` (history), `claude-haiku-5-5` (current).
- Sonnet 5.5 on the PDF remains an option for a later re-run if quality needs to go up.
- Historical note: the previous text-first, Gemini, vision-fallback design is the
  "vision fallback" this ADR removes.

## Rejected

- **Sonnet 5.5 PDF as the default.** Best judge result, but 18 times the corpus cost.
  Kept as a later option.
- **Gemini as a fallback provider.** Two code paths to test for no user.
- **Sending whole 96-page PDFs.** Past 100K tokens the long-context rate applies
  (about five times list in the probe).
- **A per-document vision pass.** The model already reads the PDF, so a second pass
  adds cost without a gap to close.
