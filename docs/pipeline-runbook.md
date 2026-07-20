# Pipeline Runbook

The NHTSA Comms pipeline is two decoupled jobs plus a digest, coupled only by
the `communications.status` column (`pending -> processed | failed`). It runs on
Marcus's machine, **on demand** — there is no nightly daemon installed. A
launchd plist is provided for reference if unattended runs are ever wanted (see
[Optional scheduling](#optional-scheduling)).

All commands run from `backend/` with `DATABASE_URL` (and, for the digest,
Gemini + Resend keys) in `backend/.env`.

## The one command

```bash
cd backend
python -m src.jobs.run_pipeline            # sync -> process -> digest (dry-run digest)
python -m src.jobs.run_pipeline --send-digest   # ... and actually send the email
```

This runs, in order:

1. **sync** — poll NHTSA per tracked vehicle, insert new communications as `pending`.
2. **process** — extract PDF text, run Gemini structured extraction, embed.
3. **digest** — email a summary of newly processed items (dry-run unless `--send-digest`).

Useful flags:

| Flag | Effect |
|------|--------|
| `--sync-only` | discovery only, no processing |
| `--skip-sync` | process + digest only (drain an existing backlog) |
| `--send-digest` | actually send the digest (default is a dry-run render) |
| `--sync-llm` | use the synchronous LLM API instead of the Batch API |

Individual stages can also be run alone:

```bash
python -m src.jobs.sync --all
python -m src.jobs.process --all           # or --stage extract|llm|embed
python -m src.jobs.digest                  # dry run; --send to send
python -m src.jobs.process --status        # counts only, no work
```

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

## Optional scheduling

Marcus runs the pipeline manually, so nothing is scheduled by default. To run it
nightly anyway:

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

launchd (not cron) is the right tool on macOS: it survives reboots and catches
up missed runs. Failure visibility for unattended runs comes from the non-zero
exit plus the `RUN COMPLETED WITH FAILURES` log line; the digest email can also
be used to notice a silent stall (no email for several days = check the logs).

## Email digest

The digest emails newly processed communications, grouped by vehicle, since the
last watermark. It requires three env vars to actually send:

```
RESEND_API_KEY=...
DIGEST_FROM_EMAIL=digest@your-verified-domain    # Resend requires a verified sender domain
DIGEST_TO_EMAIL=you@example.com
```

Without `--send-digest` it writes `digest_preview.html` for review and sends
nothing. A run with zero new items sends nothing regardless. The watermark only
advances after a successful send, so a failed send is retried in full on the
next run — never duplicated, never skipped.
