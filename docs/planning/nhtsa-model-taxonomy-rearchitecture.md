# Project Brief: Re-key vehicle discovery on NHTSA's plain-nameplate taxonomy

> **For the receiving agent:** This is a self-contained architecture-design brief.
> Run it through `/linear-project-workflow` — create the Linear project, milestones,
> and issues, then do the architecture/design work. Everything you need is below;
> you should not need the originating chat. Verify the live claims yourself (NHTSA's
> API was degraded when this was written — see §6), but treat the DB-derived evidence
> in §3 as reproduced and solid.

## 1. One-line problem

The Add-Vehicle picker is built on NHTSA's **crash-test taxonomy** (which splits every
nameplate into body-style "models" like `F-150 SUPER CREW` and needs a slow, timeout-prone
`byYmmt` lookup), but the **manufacturer communications** the app exists to serve are keyed on
the **plain nameplate** (`F-150`). We navigate the wrong taxonomy to reach the data, and the
`byYmmt` timeout is the tax for bridging the two. Re-key discovery + fetching on the plain
nameplate and both the confusing UX and the timeout bug disappear.

## 2. How we got here (symptom → root cause)

- **Symptom:** Adding e.g. a *2021 Ford F-150 SUPER CREW* shows "Finding this vehicle…" and
  spins ~indefinitely.
- **Immediate cause:** `GET /api/communications/discovery/variants` calls NHTSA
  `/vehicles/byYmmt`, whose gateway hard-kills at 30s. High-variant nameplates
  (`F-150 SUPER CREW`, `BRONCO`) consistently exceed 30s → 504; most others land just under.
- **Already shipped** (branch `feature/nhtsa-phase-b-symptom-search`, do **not** redo — build
  on it): graceful degradation so the failure is fast and honest instead of an infinite hang —
  `byYmmt` errors now surface as `503` with a "lookup timed out, try again" message, `retry: 0`
  on the query, and a regression test. Files: `backend/src/communications/nhtsa_client.py`,
  `backend/src/communications/router.py`,
  `frontend/src/features/communications/hooks/useDiscovery.ts`,
  `frontend/src/features/vehicles/components/AddVehicleModal.tsx`,
  `backend/tests/test_variants_lookup.py`.
- **Root design finding (this project):** the timeout only exists because we resolve a
  body-style "model" to a numeric `vehicleId` via `byYmmt`. The communications data doesn't
  use body styles at all.

## 3. The evidence (DB-derived — reproduced, trustworthy)

Query over the production Postgres (`communications.raw` JSON, `associatedProducts[]`):

- **1,203** communications scanned; **~30,000** vehicle associations.
- Every F-150 bulletin is keyed to `productModel: "F-150"` — **1,627 occurrences, 0** to any
  body style.
- **Zero** TSBs anywhere are keyed to a cab/body-style string (`SUPER CREW`, `REGULAR CAB`,
  `SUPER CAB`, `CREW CAB`).
- Top `productModel` values are all clean nameplates: `TAHOE`, `SILVERADO 1500`, `SIERRA 1500`,
  `YUKON`, `ESCALADE`, `COLORADO`, `CANYON`, `EQUINOX`, …
- Example TSB payload (`nhtsa_id 10137149`, "SSM 47270"): summary *"Some 2015-2018 F-150 may
  exhibit a dash panel distortion…"*, `associatedProducts` = four rows all `productModel: "F-150"`
  spanning years 2015–2018.

**Reproduce it:**
```bash
cd backend
python3 - <<'PY'
import asyncio, asyncpg, json
from collections import Counter
from dotenv import dotenv_values
url = dotenv_values('.env')['DATABASE_URL']
async def main():
    c = await asyncpg.connect(url)
    rows = await c.fetch("SELECT raw FROM communications WHERE raw IS NOT NULL")
    models = Counter()
    for r in rows:
        raw = r['raw']; raw = json.loads(raw) if isinstance(raw, str) else raw
        for p in (raw.get('associatedProducts') or []):
            if p.get('productModel'): models[p['productModel']] += 1
    print("body-style-keyed TSBs:",
          {m:n for m,n in models.items()
           if any(w in m.upper() for w in ('SUPER CREW','REGULAR CAB','SUPER CAB','CREW CAB'))}
          or "NONE")
    print("top 10:", models.most_common(10))
    await c.close()
asyncio.run(main())
PY
```
> Interpreter note: use the pyenv Python (3.11.6), **not** `backend/venv` — the venv lacks
> `asyncpg`.

## 4. The two NHTSA taxonomies (the heart of it)

| | Picker / discovery path (today) | Communications data (the actual payload) |
|---|---|---|
| Source | `/vehicles/*` — SafetyRatings/NCAP crash-test DB | `communications.raw.associatedProducts` |
| Model vocabulary | `F-150 SUPER CREW`, `F-150 REGULAR CAB`, `F-150 SUPER CAB DIESEL`, … | `F-150` (plain nameplate) |
| Key needed to fetch | numeric `vehicleId` via `/vehicles/byYmmt` (⏱ 30s timeout) | none — it's already `year/make/model` text |
| Why it's split | crash ratings genuinely differ by cab config | TSBs apply to the nameplate across year ranges |

Current fetch chain (all `/vehicles/*`): `discovery/models` → user picks body-style model →
`byYmmt(year,make,model)` → `vehicleId` → `/vehicles/{vehicleId}/details?data=…manufacturercommunications`
→ list of comm `nhtsaId`s → `/safetyIssues/byNhtsaId` per comm.

**Note the inconsistency to investigate:** NHTSA's *products* discovery endpoint
`/products/vehicle/models?modelYear=YYYY&make=ford&issueType=mc` **also** returns the granular
body-style names (84 for 2021 Ford) — yet the TSB payloads it ultimately yields use plain
`F-150`. So NHTSA is internally inconsistent: discovery is granular, the records are not.
Mapping how NHTSA collapses granular→plain is a key design task (§7 Q2).

## 5. Goal & scope of this project

**Primary goal:** discovery and fetching keyed on the **plain nameplate** so:
1. the Add-Vehicle picker shows real models (`F-150`, not five F-150 body styles), and
2. the `byYmmt` step — and its entire timeout failure mode — is eliminated.

**In scope:** NHTSA client + discovery endpoints + fetch path; vehicle identity/keying in
Postgres; the Add-Vehicle frontend flow; a data migration for existing rows; caching/resilience
given NHTSA's flakiness.

**Explicitly a backend change.** (The prior milestone in `CLAUDE.md` was a *frontend-only*
UI-beautification effort — that "no backend changes" constraint was milestone-specific and does
**not** apply here. Don't let `CLAUDE.md` mislead you on this.)

**Out of scope / non-goals:** the LLM document-processing pipeline, symptom search, billing.
Don't touch `comm_documents` embedding/LLM logic beyond what the re-keying strictly requires.

## 6. Hard constraint: validate on a good NHTSA window

When this was written, **all** of NHTSA's API (both `/vehicles/*` and `/products/*`) was
returning 504s — a temporary capacity problem on their side, not ours. Timing samples:
`F-150 SUPER CREW` no-trim → 504 at ~30s (7/7 attempts); `+trim=XL` → 200 in ~5–7s;
`CIVIC`/`CAMRY`/`MUSTANG` → 200 in ~16–25s; `BRONCO` → 504. **You must re-validate the
replacement fetch path's shape and latency on a window when NHTSA is healthy** before committing
the design. Design for their flakiness (retry-with-backoff already exists on `get_vehicle_details`
and `get_safety_issue`; the discovery/`vehicle_catalog` cache pattern is worth extending).

## 7. Open questions the design must answer

1. **Is there an NHTSA endpoint that lists manufacturer-communication `nhtsaId`s by plain
   `year/make/model` directly**, skipping `byYmmt`→`vehicleId`? Map the `/products/*` and
   `/safetyIssues/*` families. Known-good today: `/safetyIssues/byNhtsaId` (single comm, already
   used). Guessed routes that 403'd (don't exist): `/safetyIssues/byVehicle`,
   `/safetyIssues/byManufacturer`, `/products/vehicle/manufacturercommunications`. Find the real
   one, or confirm `/vehicles/{id}/details` is unavoidable and re-scope accordingly.
2. **How to present clean nameplates** when even the products discovery endpoint returns granular
   names. Options: derive the nameplate vocabulary from `associatedProducts.productModel`; strip a
   body-style suffix set; or map granular→plain. Which is authoritative and complete across makes?
3. **Vehicle identity / migration.** Today `vehicles.nhtsa_vehicle_id` (the `byYmmt` id) is the
   identity threaded through `communication_vehicles`, `refresh`, and `corpus`. Moving identity to
   `(year, make, plain_model)` is a schema + data migration. Design the new key, a backfill for
   existing rows (e.g. the tracked `2018 F-150 REGULAR CAB` → `F-150`), and backward compatibility.
4. **No data loss / no surprise changes.** Confirm that keying on plain `F-150` returns a
   superset (never fewer) of the comms a user gets today via a body-style `vehicleId`. Quantify.
5. **De-dup impact.** If a user previously could add `F-150 SUPER CREW` *and* `F-150 REGULAR CAB`
   as separate vehicles that pull overlapping TSBs, collapsing to `F-150` removes that redundancy —
   confirm that's desired and won't orphan data via `communication_vehicles`.

## 8. Relevant files & tables

**Backend**
- `backend/src/communications/nhtsa_client.py` — NHTSA client. `get_models_for_make_year`
  (`/vehicles/models`), `get_vehicle_variants` (`/vehicles/byYmmt`), `get_vehicle_details`
  (`/vehicles/{id}/details`, has retry), `get_safety_issue` (`/safetyIssues/byNhtsaId`, has retry),
  `get_trims_for_model` (`/vehicles/trims`, currently unused by UI).
- `backend/src/communications/service.py` — `get_discovery_variants` + the `vehicle_catalog`
  read-through cache (`_read_cached_variants`, `_cache_variants`).
- `backend/src/communications/router.py` — discovery endpoints (`/discovery/years|makes|models|variants|trims`).
- `backend/src/refresh/service.py` — comms fetch: `get_vehicle_details(nhtsa_vehicle_id)`.
- `backend/src/vehicles/` — vehicle CRUD; `nhtsa_vehicle_id` keying.
- `backend/migrations/` — schema history; `0006_vehicle_catalog.sql` is the YMM→vehicleId cache.

**Frontend**
- `frontend/src/features/vehicles/components/AddVehicleModal.tsx` — the cascading picker.
- `frontend/src/features/communications/hooks/useDiscovery.ts` — discovery React Query hooks.
- `frontend/src/client/communications.ts` — typed API client for discovery.

**Postgres** (see `docs/schema.md`)
- `communications` — one row per `nhtsa_id`; `raw` holds NHTSA payload incl. `associatedProducts`.
- `communication_vehicles` — many-to-many `(communication_id, vehicle_id, matched_keywords, first_seen_at)`.
- `vehicles` — tracked vehicles; `nhtsa_vehicle_id`, `year`, `make`, `model` (body-style), `trim` (e.g. `PU/RC`).
- `vehicle_catalog` — YMM→`vehicleId` cache in front of `byYmmt`.
- `comm_documents` — per-PDF extracted text / LLM output / embedding (out of scope).

## 9. Stack (for the design)

Python 3.10+/FastAPI, asyncpg, Postgres 18 + pgvector on Railway. React 19 + Vite + TypeScript,
TanStack Query, no CSS framework (CSS custom properties). `DATABASE_URL` in `backend/.env`
(gitignored — never commit/paste it). Linear is the source of truth for planning.

## 10. Suggested milestone shape (the receiving agent refines this)

1. **Discovery spike** — map NHTSA's products/safetyIssues API on a healthy window; answer §7
   Q1–Q2; produce a definitive endpoint inventory with latencies. *Investigation-only.*
2. **Target design** — new vehicle identity, fetch path, caching/resilience, and a migration
   plan; ADR-style writeup. *Design-only, no code.*
3. **Backend re-key** — new discovery + fetch keyed on plain nameplate; keep `byYmmt` only if the
   spike proves it's unavoidable.
4. **Data migration** — backfill existing `vehicles`/`communication_vehicles` to the new key.
5. **Frontend** — collapse the model picker to real nameplates; retire the "Finding this
   vehicle…" `byYmmt` resolve step (or repoint it).
6. **Validation** — no-data-loss check (§7 Q4), regression tests, live end-to-end.

## 11. Acceptance criteria

- The Add-Vehicle model dropdown lists plain nameplates (`F-150`, `SILVERADO 1500`), not
  body-style variants.
- Adding any nameplate that has TSBs succeeds without a `byYmmt`-style 30s timeout in the path.
- A tracked vehicle surfaces a superset (never fewer) of the communications it gets today.
- Existing tracked vehicles are migrated with no orphaned `communication_vehicles` rows.
- The already-shipped 503 graceful-degradation behavior is preserved or made moot (no infinite
  spinner under any NHTSA failure).
