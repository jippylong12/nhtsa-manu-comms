"""Hybrid retrieval: fusion logic (pure) and vehicle scoping (against the DB).

The fusion and tag-boost tests are pure and always run. The scoping tests need
the corpus, so they hit the real Railway database like the rest of the suite and
skip when DATABASE_URL is unset. They pass a fixed dummy unit vector as the
query embedding so they never call the embeddings API: the point is proving the
vehicle filter holds, not retrieval quality.
"""

from datetime import datetime, timezone

import pytest

from src.config import get_settings
from src.symptom_search import retrieval
from src.symptom_search.retrieval import (
    Match,
    _fuse,
    mapped_systems,
    retrieve,
    revision_stem,
)

# NHTSA vehicleIds present in the corpus.
VEH_SILVERADO_24 = 218944
VEH_EQUINOX_25 = 685101
VEH_SILVERADO_26 = 715144

# A valid 1536-d unit vector, so no embeddings API call is needed.
DUMMY_EMBEDDING = [1.0] + [0.0] * 1535


def _row(doc_id, nhtsa_id, systems=None, **kw):
    base = {
        "doc_id": doc_id,
        "nhtsa_id": nhtsa_id,
        "communication_number": kw.get("communication_number", f"NUM{doc_id}"),
        "communication_type": kw.get("communication_type", "TSB"),
        "communication_date": None,
        "llm_summary": kw.get("llm_summary", "summary"),
        "symptoms": kw.get("symptoms", []),
        "systems": systems or [],
        "components": kw.get("components", []),
        "remedy": kw.get("remedy", ""),
        "doc_kind": kw.get("doc_kind", "service_procedure"),
    }
    if "cosine_sim" in kw:
        base["cosine_sim"] = kw["cosine_sim"]
    return base


# --------------------------------------------------------------------------- #
# Pure fusion / tag-boost logic
# --------------------------------------------------------------------------- #
def test_mapped_systems_detects_multiple_systems():
    systems = set(mapped_systems("service high voltage warning and no heat"))
    assert "hvac" in systems  # "no heat"
    assert "electrical" in systems  # "high voltage"


def test_mapped_systems_empty_for_unmapped_text():
    assert mapped_systems("the quick brown fox") == []


def test_fuse_rewards_documents_in_both_lists():
    """A doc ranked #1 in both vector and FTS must beat a doc that is #1 in only
    one — that is the whole point of fusing the two signals."""
    both = _row(1, "AAA")
    vec_only = _row(2, "BBB")
    matches = _fuse("query", [both, vec_only], [both], k=5)
    ordered = [m.nhtsa_id for m in matches]
    assert ordered[0] == "AAA"
    assert ordered.index("AAA") < ordered.index("BBB")


def test_fuse_collapses_documents_to_one_per_communication():
    """Two documents of the same communication collapse to a single result."""
    doc_a = _row(1, "SAME")
    doc_b = _row(2, "SAME")
    matches = _fuse("query", [doc_a, doc_b], [], k=5)
    assert len([m for m in matches if m.nhtsa_id == "SAME"]) == 1


def test_fuse_applies_tag_boost_when_system_matches():
    plain = _row(1, "PLAIN", systems=["body"])
    braky = _row(2, "BRAKY", systems=["brakes"])
    # Both at vector rank 2 vs 1 respectively would put PLAIN ahead on RRF alone;
    # the brake query's tag boost should lift the brake-tagged doc.
    matches = _fuse("my brakes feel weak", [plain, braky], [], k=5)
    braky_match = next(m for m in matches if m.nhtsa_id == "BRAKY")
    assert "brakes" in braky_match.matched_systems
    assert braky_match.score > (retrieval.W_VECTOR / (retrieval.RRF_K + 2))


def test_fuse_respects_k():
    rows = [_row(i, f"ID{i}") for i in range(10)]
    assert len(_fuse("q", rows, [], k=3)) == 3


def test_match_as_dict_rounds_and_serialises():
    m = Match(
        nhtsa_id="X",
        communication_number="N",
        communication_type="TSB",
        communication_date=None,
        llm_summary="s",
        symptoms=["a"],
        systems=["b"],
        components=["c"],
        remedy="r",
        doc_kind="recall",
        score=0.123456789,
        cosine_sim=0.987654,
        vector_rank=1,
        fts_rank=None,
        matched_systems=["brakes"],
    )
    d = m.as_dict()
    assert d["nhtsa_id"] == "X"
    assert d["score"] == round(0.123456789, 6)
    assert d["cosine_sim"] == round(0.987654, 4)
    assert d["fts_rank"] is None


# --------------------------------------------------------------------------- #
# Bulletin revision families
# --------------------------------------------------------------------------- #
def test_revision_stem_strips_letter_revisions():
    assert revision_stem("PIT6202A") == "PIT6202"
    assert revision_stem("PIT6202") == "PIT6202"
    assert revision_stem("PIE0788C") == "PIE0788"
    assert revision_stem("PIT5832V") == "PIT5832"


def test_revision_stem_strips_campaign_revisions():
    assert revision_stem("N252521591-02") == "N252521591"
    assert revision_stem("N252521591-04") == "N252521591"


def test_revision_stem_leaves_real_numbers_intact():
    """The suffix rules must not mangle numbers whose tail is not a revision."""
    for number in ("24-NA-125", "23-NA-151", "653.30", "N242484500"):
        assert revision_stem(number) == number


def test_revision_stem_handles_missing_number():
    assert revision_stem(None) is None
    assert revision_stem("   ") is None


def _dated(doc_id, nhtsa_id, number, day):
    return _row(
        doc_id,
        nhtsa_id,
        communication_number=number,
        communication_date=datetime(2024, 9, day, tzinfo=timezone.utc),
    )


def test_fuse_collapses_revision_family_to_current_revision():
    """PIT6202/A/B/C are one bulletin; only the newest should survive."""
    rows = [
        _dated(1, "1001", "PIT6202", 9),
        _dated(2, "1002", "PIT6202A", 12),
        _dated(3, "1003", "PIT6202B", 22),
        _dated(4, "1004", "PIT6202C", 25),
    ]
    matches = _fuse("hood ajar message", rows, [], k=10)
    assert len(matches) == 1
    assert matches[0].communication_number == "PIT6202C"  # newest revision
    assert matches[0].superseded_count == 3


def test_fuse_keeps_distinct_bulletins_separate():
    rows = [
        _dated(1, "1001", "PIT6202A", 9),
        _dated(2, "1002", "24-NA-125", 12),
        _dated(3, "1003", "653.30", 22),
    ]
    matches = _fuse("anything", rows, [], k=10)
    assert len(matches) == 3
    assert all(m.superseded_count == 0 for m in matches)


def test_collapsed_family_inherits_best_score():
    """An older revision matching strongly must still rank the family highly."""
    old_strong = _dated(1, "1001", "PIT6202", 9)  # vector rank 1
    new_weak = _dated(2, "1002", "PIT6202C", 25)  # vector rank 2
    other = _dated(3, "2001", "PIT9999", 25)  # vector rank 3
    matches = _fuse("q", [old_strong, new_weak, other], [], k=10)
    family = next(m for m in matches if m.communication_number == "PIT6202C")
    # Inherited rank-1 score, so it outranks the unrelated rank-3 bulletin.
    assert family.score >= matches[0].score
    assert family.superseded_count == 1


# --------------------------------------------------------------------------- #
# Vehicle scoping (AC3) — against the real corpus
# --------------------------------------------------------------------------- #
def _needs_db():
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not configured")


async def _linked_vehicles(nhtsa_id: str) -> set[int]:
    from src.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT v.nhtsa_vehicle_id
            FROM communications c
            JOIN communication_vehicles cv ON cv.communication_id = c.id
            JOIN vehicles v ON v.id = cv.vehicle_id
            WHERE c.nhtsa_id = $1
            """,
            nhtsa_id,
        )
    return {r["nhtsa_vehicle_id"] for r in rows}


async def test_every_result_is_linked_to_the_scoped_vehicle():
    """The core scoping guarantee: nothing outside the target vehicle comes back."""
    _needs_db()
    result = await retrieve(
        VEH_EQUINOX_25,
        "service high voltage warning and no heat",
        k=10,
        embedding=DUMMY_EMBEDDING,
    )
    assert result.matches, "expected some matches for a populated vehicle"
    for m in result.matches:
        linked = await _linked_vehicles(m.nhtsa_id)
        assert VEH_EQUINOX_25 in linked, f"{m.nhtsa_id} surfaced but is not on the Equinox"


async def test_single_vehicle_document_never_leaks_across_vehicles():
    """A glass-spot bulletin that exists only for the 2024 Silverado must not be
    retrievable when the query is scoped to the Equinox."""
    _needs_db()
    only_silverado = "11006511"  # PI0754G, linked solely to 218944
    assert await _linked_vehicles(only_silverado) == {VEH_SILVERADO_24}

    scoped_other = await retrieve(
        VEH_EQUINOX_25, "spots on my windows", k=10, embedding=DUMMY_EMBEDDING
    )
    assert only_silverado not in {m.nhtsa_id for m in scoped_other.matches}


# --------------------------------------------------------------------------- #
# Eval scores at bulletin-family level
# --------------------------------------------------------------------------- #
def test_matches_expected_accepts_a_newer_revision_of_the_same_bulletin():
    """Retrieval returns the current revision, so the eval must not demand the
    historical nhtsa_id the golden set happened to record."""
    from src.eval.run_eval import matches_expected

    # Expected PIT6018F; retrieval returned the newer PIT6018J under a new id.
    assert matches_expected("99999999", "PIT6018J", {"10242304"}, {"PIT6018"})


def test_matches_expected_still_matches_on_exact_id():
    from src.eval.run_eval import matches_expected

    assert matches_expected("10242304", None, {"10242304"}, set())


def test_matches_expected_rejects_an_unrelated_bulletin():
    from src.eval.run_eval import matches_expected

    assert not matches_expected("99999999", "24-NA-125", {"10242304"}, {"PIT6018"})


async def test_revision_count_matches_the_full_family_in_the_database():
    """The 'latest of N revisions' claim must reflect every revision the vehicle
    has, not just the ones that scored into the candidate pool."""
    _needs_db()
    from src.db import get_pool

    result = await retrieve(
        VEH_SILVERADO_24, "hood ajar message on the dash", k=6, embedding=DUMMY_EMBEDDING
    )
    families = [m for m in result.matches if m.superseded_count]
    if not families:
        pytest.skip("no revision family surfaced for this probe query")

    pool = await get_pool()
    async with pool.acquire() as conn:
        for m in families:
            stem = revision_stem(m.communication_number)
            rows = await conn.fetch(
                """
                SELECT c.nhtsa_id, c.communication_number, c.communication_date
                FROM communications c
                JOIN communication_vehicles cv ON cv.communication_id = c.id
                JOIN vehicles v ON v.id = cv.vehicle_id
                WHERE v.nhtsa_vehicle_id = $1 AND c.communication_number ILIKE $2
                """,
                VEH_SILVERADO_24,
                stem + "%",
            )
            family = [r for r in rows if revision_stem(r["communication_number"]) == stem]
            assert m.superseded_count == len(family) - 1, f"{stem}: count is not the full family"

            newest = max(
                family,
                key=lambda r: (
                    r["communication_date"] is not None,
                    r["communication_date"].timestamp() if r["communication_date"] else 0.0,
                ),
            )
            assert m.nhtsa_id == newest["nhtsa_id"], f"{stem}: not the newest revision on file"
