"""The severity report for the schema-v2 re-extraction (JIP-1580).

Runs against the real schema inside the rolled-back ``conn`` fixture; the seeded
communications use ``SR-`` ids so they are easy to tell from real rows.
"""

import pytest

from src.scripts import severity_report as sr


async def _seed(conn, nhtsa_id, number, summary, severity, doc_kind, remedy, audience):
    cid = await conn.fetchval(
        "INSERT INTO communications (nhtsa_id, communication_number, communication_type, "
        "summary, status, severity, severity_source) "
        "VALUES ($1, $2, 'MC', $3, 'processed', $4, 'classifier') RETURNING id",
        nhtsa_id,
        number,
        summary,
        severity,
    )
    await conn.execute(
        "INSERT INTO comm_documents (communication_id, url, llm_summary, doc_kind, symptoms, "
        "remedy, audience) VALUES ($1, $2, 's', $3, ARRAY['stall'], $4, $5)",
        cid,
        f"https://example.test/{nhtsa_id}.pdf",
        doc_kind,
        remedy,
        audience,
    )


@pytest.mark.asyncio
async def test_the_report_shows_both_tiers_with_examples_and_classifier_reasons(conn):
    await _seed(conn, "SR-1", "SR-NUM-RECALL", "Recall about brakes", "serious", "recall", None, "owner")
    await _seed(conn, "SR-2", "SR-NUM-INFO", "Info only note", "small", "informational", None, "dealer")

    text = await sr.build_report(conn, per_tier=1000)

    assert "## MC examples: serious" in text and "## MC examples: small" in text
    assert "SR-NUM-RECALL" in text and "SR-NUM-INFO" in text
    assert "| recall |" in text and "no escalating signal" in text
    assert "severity_source" in text and "doc_kind" in text and "audience" in text
    assert "schema-v2" in text and "status_reason" in text
