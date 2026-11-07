"""copy_schema_v2: export re-read results, apply them to another database by UPDATE only.

Runs inside the rolled-back ``conn`` fixture with namespaced rows (model year 2099). The
"destination" is the same transaction after the new columns are blanked, which is what a
production clone that was never re-read looks like.
"""

import json
import random
import uuid

import pytest

from src.scripts import copy_schema_v2 as cs

NS = f"TESTCS2-{uuid.uuid4().hex[:8]}"
VECTOR = "[" + ",".join(f"{(i % 7) / 10 + 0.123456789:.9f}" for i in range(1536)) + "]"
BLANK_DOC = """
UPDATE comm_documents SET llm_summary = NULL, doc_kind = NULL, symptoms = '{}', systems = '{}',
    components = '{}', remedy = NULL, applicability = NULL, applicability_years = '{}',
    applicability_models = '{}', applicability_conditions = NULL, audience = NULL,
    llm_model = NULL, llm_tokens_in = NULL, llm_tokens_out = NULL, llm_cost_usd = NULL,
    llm_at = NULL, embedding = NULL, embedding_model = NULL, embedded_at = NULL
WHERE communication_id = ANY($1::bigint[])
"""
BLANK_COMM = """
UPDATE communications SET status = 'pending', status_reason = NULL, processed_at = NULL,
    severity = 'small', severity_source = 'type' WHERE id = ANY($1::bigint[])
"""


async def vehicle(conn) -> int:
    return await conn.fetchval(
        "INSERT INTO vehicles (nhtsa_vehicle_id, year, make, model) "
        "VALUES ($1, 2099, $2, $3) RETURNING id",
        random.randint(10**8, 10**9),
        NS,
        uuid.uuid4().hex[:6],
    )


async def comm(conn, vehicle_id, *, status="processed", summarised=True, docs=2) -> int:
    nid = f"{NS}-{uuid.uuid4().hex[:6]}"
    cid = await conn.fetchval(
        """
        INSERT INTO communications (nhtsa_id, communication_type, communication_date, status,
            status_reason, attempts, processed_at, severity, severity_source)
        VALUES ($1, 'TSB', '2099-01-01', $2, 'why', 1, now(), 'serious', 'classifier') RETURNING id
        """,
        nid,
        status,
    )
    await conn.execute(
        "INSERT INTO communication_vehicles (communication_id, vehicle_id) VALUES ($1, $2)",
        cid,
        vehicle_id,
    )
    for i in range(docs):
        await conn.execute(
            """
            INSERT INTO comm_documents (communication_id, url, extracted_text, extracted_at,
                llm_summary, doc_kind, symptoms, systems, components, remedy, applicability,
                applicability_years, applicability_models, applicability_conditions, audience,
                llm_model, llm_tokens_in, llm_tokens_out, llm_cost_usd, llm_at,
                embedding, embedding_model, embedded_at)
            VALUES ($1, $2, 'text', now(), $3, $4, '{rattle,"a b"}', '{brakes}', '{pad}', 'fix',
                'all', '{2020,2021}', '{F-150}', 'when hot', 'owner', 'haiku', 10, 5,
                0.00123456, now(), $5::vector, 'emb', now())
            """,
            cid,
            f"https://{NS}/{cid}/{i}.pdf",
            "summary" if summarised else None,
            "recall" if summarised else None,
            VECTOR if summarised else None,
        )
    return cid


async def snapshot(conn, comm_ids) -> list:
    comms = await conn.fetch(
        "SELECT id, status, status_reason, processed_at, severity, severity_source "
        "FROM communications WHERE id = ANY($1::bigint[]) ORDER BY id",
        comm_ids,
    )
    docs = await conn.fetch(
        "SELECT d.id, d.url, d.llm_summary, d.doc_kind, d.symptoms, d.systems, d.components, "
        "d.remedy, d.applicability, d.applicability_years, d.applicability_models, "
        "d.applicability_conditions, d.audience, d.llm_model, d.llm_tokens_in, d.llm_tokens_out, "
        "d.llm_cost_usd, d.llm_at, d.embedding::text AS embedding, d.embedding_model, "
        "d.embedded_at FROM comm_documents d WHERE d.communication_id = ANY($1::bigint[]) "
        "ORDER BY d.id",
        comm_ids,
    )
    return [[dict(r) for r in comms], [dict(r) for r in docs]]


# --- (a) export ----------------------------------------------------------------------------


async def test_export_has_the_processed_summarised_records_of_the_vehicle_only(conn):
    v6, other = await vehicle(conn), await vehicle(conn)
    good = await comm(conn, v6)
    await comm(conn, v6, status="pending")
    await comm(conn, v6, summarised=False)
    await comm(conn, other)

    payload = await cs.export_records(conn, [v6])
    payload = json.loads(json.dumps(payload))  # must survive a JSON file

    assert payload["schema_version"] == 2
    assert payload["vehicles"] == [v6]
    assert payload["source_counts"] == {"communications": 1, "documents": 2}
    (rec,) = payload["communications"]
    nid = await conn.fetchval("SELECT nhtsa_id FROM communications WHERE id=$1", good)
    assert rec["nhtsa_id"] == nid
    assert (rec["status"], rec["status_reason"]) == ("processed", "why")
    assert (rec["severity"], rec["severity_source"]) == ("serious", "classifier")
    assert len(rec["documents"]) == 2
    doc = rec["documents"][0]
    assert doc["url"].startswith(f"https://{NS}/")
    assert doc["symptoms"] == ["rattle", "a b"] and doc["applicability_years"] == [2020, 2021]
    assert doc["embedding"].startswith("[") and doc["llm_cost_usd"] == "0.00123456"
    assert set(doc) == {"url", *cs.DOC_COLUMNS}


# --- (b) round trip ------------------------------------------------------------------------


async def test_dry_run_changes_nothing_and_apply_restores_everything_exactly(conn):
    v = await vehicle(conn)
    ids = [await comm(conn, v), await comm(conn, v)]
    original = await snapshot(conn, ids)
    payload = json.loads(json.dumps(await cs.export_records(conn, [v])))

    await conn.execute(BLANK_DOC, ids)
    await conn.execute(BLANK_COMM, ids)
    blanked = await snapshot(conn, ids)
    assert blanked != original

    code, report = await cs.apply_records(conn, payload)  # dry run
    assert code == 0
    assert (report.communications_updated, report.documents_updated) == (2, 4)
    assert await snapshot(conn, ids) == blanked

    code, report = await cs.apply_records(conn, payload, apply=True)
    assert code == 0 and report.skipped == []
    assert await snapshot(conn, ids) == original  # embedding, arrays, timestamps, cost

    code, report = await cs.apply_records(conn, payload, apply=True)  # idempotent
    assert code == 0
    assert (report.communications_updated, report.documents_updated) == (0, 0)
    assert (report.communications_identical, report.documents_identical) == (2, 4)


# --- (c) skips -----------------------------------------------------------------------------


async def _two_comms_one_missing(conn):
    v = await vehicle(conn)
    keep, gone = await comm(conn, v), await comm(conn, v)
    payload = json.loads(json.dumps(await cs.export_records(conn, [v])))
    gone_nid = await conn.fetchval("SELECT nhtsa_id FROM communications WHERE id=$1", gone)
    await conn.execute(BLANK_DOC, [keep])
    await conn.execute(BLANK_COMM, [keep])
    await conn.execute("DELETE FROM communication_vehicles WHERE communication_id=$1", gone)
    await conn.execute("DELETE FROM comm_documents WHERE communication_id=$1", gone)
    await conn.execute("DELETE FROM communications WHERE id=$1", gone)
    return keep, gone_nid, payload


async def test_a_missing_communication_is_skipped_listed_and_nothing_is_written(conn):
    keep, gone_nid, payload = await _two_comms_one_missing(conn)
    before = await snapshot(conn, [keep])

    code, report = await cs.apply_records(conn, payload, apply=True)

    assert code == 1
    assert report.skipped == [f"communication {gone_nid}"]
    assert await snapshot(conn, [keep]) == before  # rolled back, never half-applied


async def test_allow_skips_commits_only_what_matched(conn):
    keep, gone_nid, payload = await _two_comms_one_missing(conn)

    code, report = await cs.apply_records(conn, payload, apply=True, allow_skips=True)

    assert code == 1 and report.skipped == [f"communication {gone_nid}"]
    comms, docs = await snapshot(conn, [keep])
    assert comms[0]["status"] == "processed"
    assert all(d["llm_summary"] == "summary" for d in docs)


async def test_a_missing_document_is_skipped_and_listed_not_inserted(conn):
    v = await vehicle(conn)
    cid = await comm(conn, v)
    payload = json.loads(json.dumps(await cs.export_records(conn, [v])))
    await conn.execute(BLANK_DOC, [cid])
    gone_url = payload["communications"][0]["documents"][0]["url"]
    await conn.execute("DELETE FROM comm_documents WHERE url=$1", gone_url)

    code, report = await cs.apply_records(conn, payload, apply=True, allow_skips=True)

    nid = payload["communications"][0]["nhtsa_id"]
    assert code == 1 and report.skipped == [f"document {nid} {gone_url}"]
    assert await conn.fetchval("SELECT count(*) FROM comm_documents WHERE url=$1", gone_url) == 0
    assert await conn.fetchval("SELECT count(*) FROM comm_documents WHERE communication_id=$1", cid) == 1


# --- (d) nothing else is touched -----------------------------------------------------------


async def test_other_vehicles_and_unrelated_tables_are_never_modified(conn):
    v, other = await vehicle(conn), await vehicle(conn)
    mine = await comm(conn, v)
    theirs = await comm(conn, other)
    payload = json.loads(json.dumps(await cs.export_records(conn, [v])))
    await conn.execute(BLANK_DOC, [mine, theirs])
    await conn.execute(BLANK_COMM, [mine, theirs])
    await conn.execute(
        "INSERT INTO usage_events (kind, cost_usd, meta) VALUES ('llm_extract', 1.5, $1::jsonb)",
        json.dumps({"ns": NS}),
    )
    run = await conn.fetchval(
        "INSERT INTO pipeline_runs (job, finished_at, ok, counts) "
        "VALUES ('llm', now(), true, '{}'::jsonb) RETURNING id"
    )
    ledger_before = await conn.fetch("SELECT * FROM usage_events WHERE meta->>'ns' = $1", NS)
    theirs_before = await snapshot(conn, [theirs])
    links_before = await conn.fetchval("SELECT count(*) FROM communication_vehicles")
    bulletins_before = await conn.fetchval("SELECT count(*) FROM vehicle_bulletins")
    vehicles_before = await conn.fetch("SELECT * FROM vehicles WHERE id = ANY($1)", [v, other])

    code, _ = await cs.apply_records(conn, payload, apply=True)

    assert code == 0
    assert (await snapshot(conn, [mine]))[0][0]["status"] == "processed"
    assert await snapshot(conn, [theirs]) == theirs_before
    assert await conn.fetch("SELECT * FROM usage_events WHERE meta->>'ns' = $1", NS) == ledger_before
    assert await conn.fetchval("SELECT count(*) FROM pipeline_runs WHERE id=$1", run) == 1
    assert await conn.fetchval("SELECT count(*) FROM communication_vehicles") == links_before
    assert await conn.fetchval("SELECT count(*) FROM vehicle_bulletins") == bulletins_before
    assert await conn.fetch("SELECT * FROM vehicles WHERE id = ANY($1)", [v, other]) == vehicles_before


def test_the_only_write_statements_are_the_two_scoped_updates():
    sql = " ".join([cs._UPDATE_COMM, cs._UPDATE_DOC]).upper()
    assert sql.count("UPDATE ") == 2
    for word in ("INSERT", "DELETE", "TRUNCATE", "DROP"):
        assert word not in sql
    assert "UPDATE COMMUNICATIONS SET" in sql and "UPDATE COMM_DOCUMENTS SET" in sql
    assert "USAGE_EVENTS" not in sql and "REFRESH_RUNS" not in sql


# --- (e) preconditions ---------------------------------------------------------------------


async def test_the_destination_check_passes_on_a_migrated_database(conn):
    assert await cs.check_destination(conn) == []


class _OldDb:
    """A destination without the v2 columns (no row in information_schema)."""

    async def fetch(self, *_a):
        return []


async def test_missing_v2_columns_are_named_and_stop_the_apply():
    assert await cs.check_destination(_OldDb()) == [
        "comm_documents.applicability_years",
        "comm_documents.audience",
        "communications.severity",
    ]
    with pytest.raises(cs.PreconditionError, match="communications.severity"):
        await cs.apply_records(_OldDb(), {"schema_version": 2, "communications": []}, apply=True)


async def test_a_file_that_is_not_a_v2_export_is_refused(conn):
    with pytest.raises(cs.PreconditionError):
        await cs.apply_records(conn, {"communications": []})


async def test_a_destination_document_outside_the_export_skips_that_whole_communication(conn):
    """Sync can attach a new PDF on production after the clone and reopen the communication.
    Forcing it to `processed` would leave that document unread forever (the LLM stage only
    reads `pending`), so the communication is skipped and listed, status untouched."""
    v = await vehicle(conn)
    cid = await comm(conn, v)
    payload = json.loads(json.dumps(await cs.export_records(conn, [v])))
    await conn.execute(BLANK_DOC, [cid])
    await conn.execute(BLANK_COMM, [cid])
    extra_url = f"https://{NS}/{cid}/late.pdf"
    await conn.execute(
        "INSERT INTO comm_documents (communication_id, url) VALUES ($1, $2)", cid, extra_url
    )

    code, report = await cs.apply_records(conn, payload, apply=True, allow_skips=True)

    nid = payload["communications"][0]["nhtsa_id"]
    assert code == 1
    assert report.skipped == [f"communication {nid} has 1 document(s) not in the export: {extra_url}"]
    row = await conn.fetchrow("SELECT status, severity_source FROM communications WHERE id=$1", cid)
    assert (row["status"], row["severity_source"]) == ("pending", "type")
    assert await conn.fetchval(
        "SELECT count(*) FROM comm_documents WHERE communication_id=$1 AND llm_summary IS NOT NULL",
        cid,
    ) == 0
