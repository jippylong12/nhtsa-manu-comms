# Pipeline Runbook

The NHTSA Comms pipeline is two decoupled jobs plus the alerts stage, coupled only by
the `communications.status` column (`pending -> processed | failed`). In production it runs
nightly as a Railway cron service (see [Scheduling](#scheduling)); the same commands run on
demand from Marcus's machine. A launchd plist is kept as the local alternative.

All commands run from `backend/` with `DATABASE_URL` (and, for alerts, Claude, Gemini and
Resend keys) in `backend/.env`. Tests fake Firebase and Stripe, so no test needs either
account.

## The one command

```bash
cd backend
python -m src.jobs.run_pipeline            # sync -> process -> alerts (dry-run alerts)
python -m src.jobs.run_pipeline --send-digest   # ... and actually send the alert emails (alias --send-alerts)
```

This runs, in order:

1. **sync** — look up each tracked vehicle's communications in the flat-file index, fetch the new ones from NHTSA by ID, insert them as `pending`.
2. **process** — extract PDF text locally, read each PDF with Claude Haiku 5.5 (structured extraction), embed.
   The LLM step first collects any batch an earlier run left running at Anthropic (tracked in `llm_batches`) and sends nothing new while one is still out, so no document is paid for twice.
3. **alerts** — email each eligible user the bulletins NHTSA newly published for their cars, then mark them announced (dry-run unless `--send-digest`; see [Alerts](#alerts)).

Useful flags:

| Flag | Effect |
|------|--------|
| `--sync-only` | discovery only, no processing |
| `--skip-sync` | process + alerts only (drain an existing backlog) |
| `--send-digest` (alias `--send-alerts`) | actually send the alert emails (default is a dry-run render) |
| `--sync-llm` | read documents live (synchronous Messages API, full price) instead of through the Batch API (half price, but a batch can take 25+ minutes) |

Individual stages can also be run alone:

```bash
python -m src.jobs.sync --all
python -m src.jobs.process --all           # or --stage extract|llm|embed
python -m src.alerts                       # dry run; --send to send (see Alerts)
python -m src.jobs.process --status        # counts only, no work
```

## Model and keys

Extraction uses `CLAUDE_API_KEY` (or `ANTHROPIC_API_KEY`). `GEMINI_API_KEY` is still
needed for embeddings and for the symptom-search answers. The in-app refresh reads
documents live; `process --all` and `run_pipeline` use the Batch API unless
`--sync-llm` is passed. A missing, revoked or out-of-credit Claude key stops the
run without marking any communication failed. Why the model reads the PDF itself,
and how long documents are handled: [ADR 0002](adr/0002-haiku-pdf-direct-extraction.md).

## Re-extracting the corpus

```bash
python -m src.jobs.process --reextract --cap-usd 5            # whole corpus, Batch API
python -m src.jobs.process --reextract --vehicle 12 --sync     # one vehicles.id, live
```

It prints a plan (documents to reset, estimated cost, the cap) and asks before
changing anything (`--yes` skips the prompt). The reset clears the LLM fields and
embeddings of every processed document in scope and sends those communications back
to `pending`; summary-only bulletins are left alone. It then re-reads in slices of 50,
checking the `usage_events` spend since the reset against `--cap-usd` between slices,
and re-embeds. `--limit` is refused: the reset always covers the whole scope, so
narrow a trial run with `--vehicle`.

Batch (the default) is one submission, not slices: the run estimates the cost (per-document
cost x pending documents, halved for Batch pricing) and stops with exit `3` before
submitting anything if that estimate reaches `--cap-usd`. Otherwise it submits every pending
document in a single call and logs the batch ids at INFO (`SUBMITTED ... --collect <id>`),
then polls every 60 s and waits up to 25 hours, because Anthropic's batch window is 24 hours
and a batch can sit in its queue a long time; a progress line is logged every 10 minutes. The
ledger is checked against the cap once, after collection. `--sync` keeps the 50-document
slices with a cap check between them.

If the session dies while waiting, the batch keeps running. Collect it with
`python -m src.jobs.process --collect <batch id>` (ids are in the log and the `llm` row of
`pipeline_runs`), then finish the run with `--reextract --resume` (below).

Exit codes:

- `0` clean: no stage reported failures and, afterwards, no in-scope communication is
  `failed`, no document lacks a summary and none lacks an embedding.
- `1` declined at the prompt, a slice made no progress, any stage reported `failed > 0`,
  or rows were left stranded (a network drop that fails PDF downloads, LLM calls or
  embeddings looks like this). The `reextract` row in `pipeline_runs` has `ok = false`
  and an `error` such as `extract failed=152; embed failed=1208; 152 communications
  failed; 152 documents without a summary`, and the same line is logged at ERROR. Its
  counts carry `failed`, `without_summary` and `unembedded`.
- `3` the cap was reached (`error = 'cap reached'`).

After a `1` or a `3` nothing already re-read is lost. Do not run `--reextract` again
without `--resume`: it would reset the documents already re-read. Finish the run with:

```bash
python -m src.jobs.process --reextract --resume --cap-usd 5
```

`--resume` skips the reset, still prints a plan and asks `Proceed? [y/N]` (`--yes`
skips it), then re-queues to `pending` the communications that `failed` with a reason
starting `pdf unavailable:`, `LLM call failed:` or `LLM stage error:` (it prints how many),
and runs extract, the LLM slices (the cap watermark starts fresh) and embed over whatever
is `pending`. `LLM schema violation` and `batch ` failures are left `failed`, so a resume
that cannot fix them still exits `1`. It takes `--vehicle`, `--sync` and `--cap-usd`; like
`--reextract`, it refuses `--limit`, and it is an error without `--reextract`.

## Copying re-read results from the replica to production

Use this instead of re-reading a vehicle on production when it was already re-read on the
replica (a clone, so `nhtsa_id`s and urls match). It spends no model money and replaces
re-reading on production. It never wipes tables: it only UPDATEs the new-extraction columns
(summary, structured fields, model/cost, embedding, status, severity) of rows that already
exist, matched by `nhtsa_id` and document url. It never inserts or deletes, and never touches
`usage_events`, `refresh_runs`, `vehicle_bulletins`, `vehicles` or `communication_vehicles`.

1. Migrate production to schema v2 (migrations 0010 to 0012):
   ```bash
   cd backend && set -a && source .env && set +a && python -m src.db.migrate
   ```
2. Export from the replica (read-only):
   ```bash
   cd backend && set -a && source .env && set +a && DATABASE_URL="$TEST_DATABASE_URL" python -m src.scripts.copy_schema_v2 export --vehicle 6 --vehicle 7 --out /tmp/schema-v2-v6-v7.json
   ```
3. Dry run on production (writes nothing), then apply:
   ```bash
   cd backend && set -a && source .env && set +a && python -m src.scripts.copy_schema_v2 apply --in /tmp/schema-v2-v6-v7.json
   pg_dump -t communications -t comm_documents "$DATABASE_URL" > /tmp/before-schema-v2.sql
   python -m src.scripts.copy_schema_v2 apply --in /tmp/schema-v2-v6-v7.json --apply
   ```

Exit 0: all matched. Exit 1: some records are missing at the destination; they are listed and
nothing is written (add `--allow-skips` with `--apply` to commit only the matched rows).
Exit 2: production lacks the v2 columns; run step 1. All updates run in one transaction.

## Flat-file index

The picker reads `comm_vocab` (year/make/model per window) and sync reads
`vehicle_bulletins` (the bulletins of each tracked vehicle, found by scanning NHTSA's daily
manufacturer-communications flat files for tracked identities only). `vehicle_scans` records
which window etag each vehicle was scanned at, and `flatfile_ingests` records each window's
ETag. From `backend/`:

```bash
python -m src.communications.flatfile --bootstrap             # rebuild every window and scan every tracked vehicle (ignores ETags)
python -m src.communications.flatfile --refresh               # repair stale windows and stale vehicle scans
python -m src.communications.flatfile --scan --vehicle N      # scan one tracked vehicle against every window it is stale for
python -m src.communications.flatfile --status                # windows, vehicle scans, table and cache sizes, no work
```

`--bootstrap`, `--refresh` and `--scan` take `--window 2020-2024` to restrict to one window.
Downloaded zips are cached in `backend/.flatfile_cache/` (gitignored, safe to delete: the
next run downloads again). Set `FLATFILE_CACHE_DIR` to put the cache elsewhere.

You rarely need these by hand. The in-app refresh and `run_pipeline` both run the ETag
check and scan before sync, so sync always sees a current `vehicle_bulletins`. A failed
window leaves that window's previous scan in place. The in-app refresh then carries on with
a warning, except for a vehicle that has never been scanned: its run fails with "Could not
read NHTSA's bulletin files" before sync. `run_pipeline` still runs sync on a failed scan
but exits 1. Scans of the same window from different processes take turns (a Postgres
advisory lock per window), so a scan on add and a refresh run never collide. `run_pipeline` takes
`--skip-scan` to skip the scan stage (`--skip-sync` skips both scan and sync). Adding a
vehicle also starts a background scan for it. The first refresh of a brand-new vehicle
takes a minute or two, because it has to read every window once.

### One-time backfill to the plain nameplate

Existing tracked vehicles keep their old model strings until the backfill maps
them to the plain nameplate (and merges any that collide). It needs a populated
index, so bootstrap first; `--apply` refuses to run when `comm_vocab` is empty.

```bash
python -m src.scripts.backfill_plain_nameplate --dry-run   # report only (the default)
python -m src.scripts.backfill_plain_nameplate --apply     # write, in one transaction
```

Add `--json` to either to emit the report as JSON. Read the dry-run report before
applying.

### Identity check

After the backfill (and a sync for any vehicle that was missing bulletins), prove
nothing was lost:

```bash
python -m src.scripts.identity_check --baseline tests/fixtures/identity_baseline_2026-07-24.json
```

It prints, per active vehicle, the linked communications, the communications the
index lists for that identity, and the delta against the baseline. It exits 1 if
any join row is orphaned, any active identity is duplicated, any vehicle has
fewer linked communications than its baseline, or any vehicle in the baseline is
no longer active; `--baseline` is optional and
`--json` emits the report as JSON.

## Checking the last run

```bash
ls -t backend/.pipeline_logs/run-*.log | head -1     # newest run log
tail -n 40 "$(ls -t backend/.pipeline_logs/run-*.log | head -1)"
```

A run ends with exactly one of:

- `RUN COMPLETED CLEANLY` (exit 0)
- `RUN COMPLETED WITH FAILURES: ...` (exit 1) — the message lists which stage(s) failed
- `another pipeline run is active ...` (exit 2) — the lockfile was held

Per-run logs are kept for **30 days** and pruned automatically at the start of
each run. Each run also writes token counts and cost to the `pipeline_runs`
table:

```sql
SELECT job, finished_at, ok, counts, cost_usd FROM pipeline_runs
ORDER BY id DESC LIMIT 10;
```

## Failure handling and recovery

The pipeline is **crash-safe by construction**, because `status` is the only
state that matters:

- A communication with no processed documents stays `pending`; the next run
  drains it.
- A download/extraction/LLM failure marks the communication `failed` with a
  `status_reason`; it does not block others.
- A run killed mid-way (Ctrl-C, power loss, `kill`) leaves rows in
  `pending`/`failed`. There is nothing to clean up — just run again.
- Re-running never double-charges: sync never resets a `processed` row, and the
  processor only picks up documents without an LLM summary.

To retry `failed` communications (e.g. after a transient NHTSA outage):

```sql
UPDATE communications SET status='pending', status_reason=NULL
WHERE status='failed' AND status_reason LIKE '%download%';
```

then run the pipeline again.

### Overlapping runs

A PID lockfile at `backend/.pipeline.lock` prevents two runs at once. A run that
finds a live lock exits with code 2. A stale lock (the holding process is gone)
is removed automatically.

## Scheduling

**Railway cron (the production schedule).** The `nightly` service runs
`python -m src.jobs.run_pipeline --send-digest` at `0 9 * * *` (UTC) from the same Docker
image as the API. It runs to completion and exits; a run is skipped while the previous one
is still going. Service settings and variables are in [deploy.md](deploy.md). A run that
fails exits non-zero, which Railway shows as a failed run, and the log ends with
`RUN COMPLETED WITH FAILURES`.

**launchd (the local alternative).** To run the pipeline nightly from a Mac instead:

1. Edit `backend/deploy/com.nhtsa-comms.pipeline.plist`: replace
   `ABSOLUTE_REPO_PATH` and `PYTHON_PATH`, set the desired `Hour`/`Minute`.
2. Install:
   ```bash
   cp backend/deploy/com.nhtsa-comms.pipeline.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.nhtsa-comms.pipeline.plist
   ```
3. Uninstall:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.nhtsa-comms.pipeline.plist
   rm ~/Library/LaunchAgents/com.nhtsa-comms.pipeline.plist
   ```

Use one schedule per database, not both.

## Alerts

Alerts email every account with a car, free or paid: once when a car it was waiting on finishes its first read, and again when NHTSA publishes new bulletins for it (a free account gets those as a teaser with an unlock button). A car's history is never mailed and a night with nothing new sends nothing. A dry run does not stamp a car ready, so it shows only cars a sending pass already stamped.
The rules (watermark,
revision folding, "Now ready", unsubscribe) are in [product.md](product.md#alerts).

```bash
cd backend
python -m src.alerts                                  # dry run for everyone: writes one HTML file per user with news, sends nothing
python -m src.alerts --send                           # send, advance each sent user's watermark, and mark the bulletins announced
python -m src.alerts --only-email you@example.com     # restrict to one user by email
python -m src.alerts --only-user-id 3                 # restrict to one user by id
python -m src.alerts --out-dir /tmp/alerts            # where the dry run writes its HTML (default: current directory)
```

It prints who would receive mail (or did), the destination address, item counts and the
outcome, never a key. A brand-new subscriber with no watermark gets only news processed after they
subscribed; an admin with no subscription is baselined silently on the first `--send` pass
(nothing is mailed, the pass time becomes their watermark), so a dry run before that pass
shows no items for them. The watermark advances only after a successful send, so a failed send is
retried in full on the next run.

Sending needs `RESEND_API_KEY`, `DIGEST_FROM_EMAIL` (an address on a domain verified in
Resend) and `APP_BASE_URL` (the public origin, for the unsubscribe link). `DIGEST_TO_EMAIL` is
legacy and no longer decides who is mailed. `ALERT_TEST_RECIPIENT=you@example.com` sends every
alert to that one address instead of the user's, for proofs on the replica; leave it unset in
production. `python -m src.jobs.digest` still works as a shim that runs the alert pass for
the admin only.

## Prefill and Stripe setup

Track and read the popular cars before launch (the list is `top50.json`; how to edit it and
what a run costs: [product.md](product.md#the-prefill)):

```bash
cd backend
python -m src.scripts.prefill --dry-run                          # default: print the plan and the estimate
python -m src.scripts.prefill --only 4 --dry-run                 # first 4 entries only
python -m src.scripts.prefill --apply --cap-usd 15 --yes         # create, scan, sync, read; stops at the cap
python -m src.scripts.prefill --list my.json --apply             # another list file; asks "Proceed? [y/N]" first
```

Exit `0` done, `1` failure or declined, `3` the cap was reached. Run the same command again to
finish a stopped run.

Create the two Stripe prices (idempotent: a price that already has its lookup key is reused):

```bash
cd backend
python -m src.scripts.stripe_setup --dry-run     # default: print what would be created
python -m src.scripts.stripe_setup --apply       # create or reuse; prints the two .env lines
```

It needs `STRIPE_SECRET_KEY` in `backend/.env` (a test key for test mode).

## Before real money and real mail

Do these in order. Nothing here runs by itself.

**1. Stripe test prices.** Put your Stripe test secret key in `backend/.env` (no key is there
today):

```
STRIPE_SECRET_KEY=sk_test_...
```

Then create the prices and copy the two ids it prints into `backend/.env`:

```bash
cd backend && python -m src.scripts.stripe_setup --apply
```

```
STRIPE_PRICE_UNLOCK=price_...
STRIPE_PRICE_MONTHLY=price_...
STRIPE_PRICE_ALL_ACCESS=price_...
```

This is the real test-mode prices step (JIP-1591) and it must happen before a real checkout.

**2. Stripe webhook.** Locally, forward Stripe events to the API and put the secret it prints
(`whsec_...`) in `backend/.env`:

```bash
stripe listen --forward-to localhost:8000/api/billing/webhook
```

```
STRIPE_WEBHOOK_SECRET=whsec_...
```

In production, in the Stripe dashboard add the endpoint
`https://<domain>/api/billing/webhook` and subscribe it to exactly the events the API
handles: `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
`customer.subscription.created`, `customer.subscription.updated`,
`customer.subscription.deleted`, `invoice.payment_failed`. Copy that endpoint's signing
secret into the `api` service's `STRIPE_WEBHOOK_SECRET`.

**3. Resend.** In `backend/.env` (and the `nightly` service variables):

```
RESEND_API_KEY=re_...
DIGEST_FROM_EMAIL=alerts@<a-domain-verified-in-Resend>
APP_BASE_URL=https://<public-domain>
```

If a send fails with "API key is invalid", rotate the key in the Resend dashboard and update
the variable. `APP_BASE_URL` is the public origin used for the unsubscribe link in every
email. Prove it once against the local replica (not production), so the mail goes only to you:

```bash
cd backend && set -a && source .env && set +a && DATABASE_URL="$TEST_DATABASE_URL" ALERT_TEST_RECIPIENT=you@example.com python -m src.alerts --send --only-email you@example.com
```

**4. Firebase.** Create a Firebase project with a web app and enable the sign-in methods you
want. Set, on the `api` service:

```
FIREBASE_SERVICE_ACCOUNT_JSON=<base64 of the service-account JSON file>
ADMIN_FIREBASE_UIDS=<your Firebase uid>[,<another uid>]
VITE_FIREBASE_API_KEY=...
VITE_FIREBASE_AUTH_DOMAIN=...
VITE_FIREBASE_PROJECT_ID=...
VITE_FIREBASE_APP_ID=...
```

Produce the base64 value with `base64 -i service-account.json | tr -d '\n'`. The
`VITE_FIREBASE_*` values are read at build time, so redeploy after changing them. Your uid is
shown in the Firebase console under Authentication. Add the production domain under
Authentication, Settings, Authorized domains.

**5. Production.** In this order:

1. Set the variables of the [environment matrix](deploy.md#environment-matrix) on `api` and
   `nightly` (`READ_ONLY` unset or `false`; `DATABASE_URL` the private connection string).
2. Deploy `api` (its start script migrates first).
3. Migrate by hand once to be certain, from your machine with the production `DATABASE_URL` in
   `backend/.env`:
   ```bash
   cd backend && set -a && source .env && set +a && python -m src.db.migrate
   ```
4. Prefill the top-50 cars; read the dry-run estimate first, then apply:
   ```bash
   cd backend && set -a && source .env && set +a && python -m src.scripts.prefill --dry-run
   cd backend && set -a && source .env && set +a && python -m src.scripts.prefill --apply --cap-usd 15
   ```
   `15` is the default cap; the apply prompts `Proceed? [y/N]` (add `--yes` to skip it).
5. Create the `nightly` service from the same image with start command
   `python -m src.jobs.run_pipeline --send-digest` and cron schedule `0 9 * * *`.
