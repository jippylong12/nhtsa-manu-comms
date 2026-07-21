"""Vehicle-scoped hybrid retrieval over the Phase A corpus.

Three signals are combined into one ranked list of communications:

  1. pgvector cosine similarity over the document embeddings  (semantic).
  2. Postgres full-text search over the document tsvector      (lexical).
  3. A tag boost when the query names a known vehicle system   (structured).

Vector distance and ts_rank live on incomparable scales, so the two ranked
lists are fused with Reciprocal Rank Fusion (RRF) rather than by normalising one
onto the other. RRF is rank-based, needs no per-corpus calibration, and is
robust when one signal is much stronger than the other. The tag boost is added
to the fused RRF score.

Everything is restricted to the target vehicle through communication_vehicles,
so a query for one vehicle can never surface another vehicle's-only
communications. That scoping is the correctness guarantee JIP-379 requires.

The tunable constants below (pool sizes, RRF damping, weights, tag boost) are
the knobs the JIP-381 quality pass turns; they are deliberately module-level so
tuning is a one-line change with a documented before/after.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.db import get_pool
from src.jobs.embed import embed_query, to_pgvector
from src.symptom_search.cost import QueryCost

log = logging.getLogger("symptom_search.retrieval")

# --- Retrieval parameters, tuned in JIP-381 against the 39-query golden set.
# The sweep (see docs/symptom-search-eval.md) found the semantic signal much
# stronger than lexical FTS for paraphrased symptom queries: weighting vector 4:1
# over FTS lifted hit@3 65.5%->86.2% and hit@5 82.8%->93.1%. The tag boost tested
# neutral-to-negative at these weights, so it is kept small (a system-match
# tiebreaker); values >=0.002 hurt hit@3 by promoting broad multi-system bulletins.
VECTOR_POOL = 40  # KNN candidates pulled before fusion
FTS_POOL = 40  # full-text candidates pulled before fusion
RRF_K = 60  # RRF damping constant (Cormack et al. default)
W_VECTOR = 2.0  # weight on the vector list's RRF contribution
W_FTS = 0.5  # weight on the FTS list's RRF contribution
TAG_BOOST = 0.001  # added per query-mapped system a document also carries
DEFAULT_K = 5

# Query phrasing -> vehicle system. `query` terms are matched against the user's
# words; `doc` terms against the LLM-assigned system tags (which are freeform, so
# matching is substring-based). A rule fires for a document only when both sides
# match, so an unrelated document is never boosted.
SYSTEM_RULES: list[dict[str, list[str]]] = [
    {
        "name": "brakes",
        "query": ["brake", "braking", "abs", "anti-lock", "antilock", "stability", "decelerat"],
        "doc": ["brake", "abs", "stability", "chassis"],
    },
    {
        "name": "infotainment",
        "query": [
            "radio",
            "screen",
            "display",
            "infotainment",
            "touchscreen",
            "head unit",
            "hud",
            "head-up",
            "heads up",
            "heads-up",
            "cluster",
            "google",
            "app",
            "onstar",
            "connected service",
        ],
        "doc": ["infotainment", "radio", "display", "cluster", "telematics", "hud"],
    },
    {
        "name": "powertrain",
        "query": [
            "propulsion",
            "power loss",
            "loss of power",
            "no start",
            "won't start",
            "wont start",
            "stall",
            "drivetrain",
            "motor",
            "drive unit",
            "reduced power",
        ],
        "doc": ["powertrain", "engine", "propulsion", "drivetrain"],
    },
    {
        "name": "electrical",
        "query": [
            "electrical",
            "wiring",
            "wire",
            "12 volt",
            "12-volt",
            "module",
            "fuse",
            "high voltage",
            "battery",
            "charge",
            "charging",
        ],
        "doc": ["electrical", "electric"],
    },
    {
        "name": "suspension",
        "query": ["suspension", "leveling", "air ride", "ride height", "shock", "air leveling"],
        "doc": ["suspension"],
    },
    {
        "name": "hvac",
        "query": ["heat", "hvac", "a/c", "air conditioning", "climate", "defrost", "no heat"],
        "doc": ["hvac", "climate", "heat"],
    },
    {
        "name": "tpms",
        "query": ["tpms", "tire pressure", "tire", "tyre"],
        "doc": ["tire pressure", "tpms", "tire"],
    },
    {
        "name": "steering",
        "query": ["steering", "steer", "steering wheel"],
        "doc": ["steering"],
    },
    {
        "name": "body",
        "query": [
            "door",
            "hood",
            "tailgate",
            "liftgate",
            "window",
            "glass",
            "trunk",
            "latch",
            "footwell",
            "carpet",
            "water leak",
            "leak",
            "seat",
            "mirror",
        ],
        "doc": ["body", "glass"],
    },
    {
        "name": "driver_assist",
        "query": [
            "super cruise",
            "adaptive cruise",
            "lane keep",
            "lane depart",
            "collision",
            "driver assist",
            "safety feature",
            "park assist",
            "blind zone",
            "blind spot",
            "cruise",
            "camera",
        ],
        "doc": ["driver assist", "assistance", "adas", "safety", "collision", "cruise", "camera"],
    },
]


# Manufacturers republish a bulletin under the same number with a revision
# suffix (PIT6202 -> PIT6202A -> ... -> PIT6202C), and each revision arrives as
# its own NHTSA record. Half this corpus is such revisions, so a symptom search
# would otherwise return the same bulletin four times. These patterns strip the
# revision marker to get the family stem.
#
# Deliberately conservative: a trailing letter only counts when it follows a
# digit, and a trailing -NN only when the body is a long campaign id. That keeps
# real numbers like 24-NA-125, 653.30, and N242484500 intact.
_LETTER_REVISION = re.compile(r"^(.*\d)([A-Z])$")
_DASH_REVISION = re.compile(r"^([A-Z]?\d{6,})-\d{1,2}$")


def revision_stem(number: Optional[str]) -> Optional[str]:
    """The bulletin-family stem for a communication number, or None."""
    if not number or not number.strip():
        return None
    n = number.strip().upper()
    m = _DASH_REVISION.match(n)
    if m:
        return m.group(1)
    m = _LETTER_REVISION.match(n)
    if m:
        return m.group(1)
    return n


@dataclass
class Match:
    """One retrieved communication, with its ranking provenance."""

    nhtsa_id: str
    communication_number: Optional[str]
    communication_type: Optional[str]
    communication_date: Optional[datetime]
    llm_summary: Optional[str]
    symptoms: list[str]
    systems: list[str]
    components: list[str]
    remedy: Optional[str]
    doc_kind: Optional[str]
    score: float
    cosine_sim: Optional[float]
    vector_rank: Optional[int]
    fts_rank: Optional[int]
    matched_systems: list[str]
    # How many older revisions of this bulletin were folded into this result.
    superseded_count: int = 0

    def as_dict(self) -> dict:
        return {
            "nhtsa_id": self.nhtsa_id,
            "communication_number": self.communication_number,
            "communication_type": self.communication_type,
            "communication_date": self.communication_date,
            "llm_summary": self.llm_summary,
            "symptoms": self.symptoms,
            "systems": self.systems,
            "components": self.components,
            "remedy": self.remedy,
            "doc_kind": self.doc_kind,
            "score": round(self.score, 6),
            "cosine_sim": round(self.cosine_sim, 4) if self.cosine_sim is not None else None,
            "vector_rank": self.vector_rank,
            "fts_rank": self.fts_rank,
            "matched_systems": self.matched_systems,
            "superseded_count": self.superseded_count,
        }


@dataclass
class RetrievalResult:
    query: str
    vehicle_id: int
    matches: list[Match]
    cost: QueryCost


def mapped_systems(query: str) -> list[str]:
    """The vehicle systems a query's wording points at."""
    q = query.lower()
    return [r["name"] for r in SYSTEM_RULES if any(kw in q for kw in r["query"])]


def _doc_matches_rule(rule: dict, systems: list[str]) -> bool:
    haystack = " ".join(s.lower() for s in systems)
    return any(tok in haystack for tok in rule["doc"])


def _tag_boost(query_systems: set[str], doc_systems: list[str]) -> tuple[float, list[str]]:
    """Boost a document once per query-mapped system it also carries."""
    hit = [
        r["name"]
        for r in SYSTEM_RULES
        if r["name"] in query_systems and _doc_matches_rule(r, doc_systems)
    ]
    return TAG_BOOST * len(hit), hit


_VEHICLE_SCOPE = (
    "EXISTS (SELECT 1 FROM communication_vehicles cv "
    "JOIN vehicles v ON v.id = cv.vehicle_id "
    "WHERE cv.communication_id = c.id AND v.nhtsa_vehicle_id = $2)"
)

_SELECT_COLS = (
    "d.id AS doc_id, c.nhtsa_id, c.communication_number, c.communication_type, "
    "c.communication_date, d.llm_summary, d.symptoms, d.systems, d.components, "
    "d.remedy, d.doc_kind"
)


async def _vector_candidates(conn, vec_literal: str, vehicle_id: int) -> list[dict]:
    rows = await conn.fetch(
        f"""
        SELECT {_SELECT_COLS}, 1 - (d.embedding <=> $1::vector) AS cosine_sim
        FROM comm_documents d
        JOIN communications c ON c.id = d.communication_id
        WHERE d.embedding IS NOT NULL AND {_VEHICLE_SCOPE}
        ORDER BY d.embedding <=> $1::vector
        LIMIT $3
        """,
        vec_literal,
        vehicle_id,
        VECTOR_POOL,
    )
    return [dict(r) for r in rows]


async def _fts_candidates(conn, query: str, vehicle_id: int) -> list[dict]:
    # plainto_tsquery keeps user input from ever being parsed as tsquery
    # operators; $2 stays the vehicle id so the scope clause is shared verbatim.
    rows = await conn.fetch(
        f"""
        SELECT {_SELECT_COLS},
               ts_rank(d.search_tsv, plainto_tsquery('english', $1)) AS fts_rank
        FROM comm_documents d
        JOIN communications c ON c.id = d.communication_id
        WHERE d.search_tsv @@ plainto_tsquery('english', $1) AND {_VEHICLE_SCOPE}
        ORDER BY fts_rank DESC
        LIMIT $3
        """,
        query,
        vehicle_id,
        FTS_POOL,
    )
    return [dict(r) for r in rows]


def _fuse(
    query: str,
    vec_rows: list[dict],
    fts_rows: list[dict],
    k: int,
) -> list[Match]:
    """Reciprocal-rank-fuse the two candidate lists, apply the tag boost, and
    collapse to one entry per communication (its best-scoring document)."""
    q_systems = set(mapped_systems(query))

    # Per document: accumulate RRF contributions and keep the richest metadata.
    docs: dict[int, dict] = {}

    def _ensure(row: dict) -> dict:
        entry = docs.get(row["doc_id"])
        if entry is None:
            entry = {
                "row": row,
                "rrf": 0.0,
                "vector_rank": None,
                "fts_rank": None,
                "cosine_sim": None,
            }
            docs[row["doc_id"]] = entry
        return entry

    for rank, row in enumerate(vec_rows, start=1):
        e = _ensure(row)
        e["rrf"] += W_VECTOR / (RRF_K + rank)
        e["vector_rank"] = rank
        e["cosine_sim"] = row.get("cosine_sim")

    for rank, row in enumerate(fts_rows, start=1):
        e = _ensure(row)
        e["rrf"] += W_FTS / (RRF_K + rank)
        e["fts_rank"] = rank

    # Score each document, then keep the top-scoring document per nhtsa_id.
    best_by_comm: dict[str, Match] = {}
    for e in docs.values():
        row = e["row"]
        systems = list(row.get("systems") or [])
        boost, matched = _tag_boost(q_systems, systems)
        score = e["rrf"] + boost
        match = Match(
            nhtsa_id=row["nhtsa_id"],
            communication_number=row.get("communication_number"),
            communication_type=row.get("communication_type"),
            communication_date=row.get("communication_date"),
            llm_summary=row.get("llm_summary"),
            symptoms=list(row.get("symptoms") or []),
            systems=systems,
            components=list(row.get("components") or []),
            remedy=row.get("remedy"),
            doc_kind=row.get("doc_kind"),
            score=score,
            cosine_sim=e["cosine_sim"],
            vector_rank=e["vector_rank"],
            fts_rank=e["fts_rank"],
            matched_systems=matched,
        )
        existing = best_by_comm.get(match.nhtsa_id)
        if existing is None or match.score > existing.score:
            best_by_comm[match.nhtsa_id] = match

    # Collapse revision families before truncating, so folding four revisions
    # into one frees a slot for a genuinely different bulletin.
    ranked = sorted(_collapse_revisions(best_by_comm.values()), key=lambda m: m.score, reverse=True)
    return ranked[:k]


def _revision_recency(m: Match) -> tuple:
    """Sort key picking the current revision of a bulletin family.

    Newest publication date wins; the number breaks ties (PIT6202C > PIT6202A).
    ``timestamp()`` avoids comparing naive against timezone-aware datetimes.
    """
    d = m.communication_date
    return (d is not None, d.timestamp() if d else 0.0, m.communication_number or "")


def _collapse_revisions(matches) -> list[Match]:
    """Keep one entry per bulletin family: the current revision.

    The family inherits the best score any of its revisions scored, so an older
    revision matching strongly still ranks the family highly, and records how
    many revisions it stands in for.
    """
    families: dict[str, list[Match]] = {}
    for m in matches:
        stem = revision_stem(m.communication_number)
        # Unnumbered communications can't be grouped; key them by their own id.
        families.setdefault(stem or f"\0id\0{m.nhtsa_id}", []).append(m)

    collapsed: list[Match] = []
    for members in families.values():
        current = max(members, key=_revision_recency)
        if len(members) > 1:
            current.score = max(x.score for x in members)
            current.superseded_count = len(members) - 1
        collapsed.append(current)
    return collapsed


async def _resolve_revision_families(conn, vehicle_id: int, matches: list[Match]) -> list[Match]:
    """Correct each match against its complete revision family in the database.

    ``_collapse_revisions`` can only see the revisions that scored into the
    candidate pool, so its "current revision" and count are relative to that
    sample. This re-resolves both against every revision the vehicle actually
    has: if a newer revision exists but was never retrieved, the match is
    swapped for it, and ``superseded_count`` becomes the true family size minus
    one. Without this, a card could claim "latest of 4 revisions" while a fifth,
    newer revision sat in the corpus unmentioned.

    The lookup stays scoped to the vehicle, so the substitution can never
    introduce a communication that vehicle does not have.
    """
    wanted = {revision_stem(m.communication_number) for m in matches if m.communication_number}
    wanted.discard(None)
    if not wanted:
        return matches

    # Prefix match narrows the scan; revision_stem below is the real authority,
    # so an over-broad LIKE only costs a few extra rows.
    rows = await conn.fetch(
        """
        SELECT c.nhtsa_id, c.communication_number, c.communication_type,
               c.communication_date, d.llm_summary, d.symptoms, d.systems,
               d.components, d.remedy, d.doc_kind
        FROM communications c
        JOIN communication_vehicles cv ON cv.communication_id = c.id
        JOIN vehicles v ON v.id = cv.vehicle_id
        LEFT JOIN LATERAL (
            SELECT llm_summary, symptoms, systems, components, remedy, doc_kind
            FROM comm_documents dd WHERE dd.communication_id = c.id
            ORDER BY dd.id LIMIT 1
        ) d ON true
        WHERE v.nhtsa_vehicle_id = $1
          AND c.communication_number IS NOT NULL
          AND c.communication_number ILIKE ANY($2::text[])
        """,
        vehicle_id,
        [s + "%" for s in wanted],
    )

    families: dict[str, list[dict]] = {}
    for r in rows:
        stem = revision_stem(r["communication_number"])
        if stem in wanted:
            families.setdefault(stem, []).append(dict(r))

    def _recency(row: dict) -> tuple:
        d = row.get("communication_date")
        return (d is not None, d.timestamp() if d else 0.0, row.get("communication_number") or "")

    for m in matches:
        family = families.get(revision_stem(m.communication_number) or "")
        if not family:
            continue
        m.superseded_count = len(family) - 1
        newest = max(family, key=_recency)
        if newest["nhtsa_id"] == m.nhtsa_id:
            continue
        # A newer revision exists that retrieval never saw; show it instead. The
        # score is kept: it is why this bulletin surfaced at all.
        m.nhtsa_id = newest["nhtsa_id"]
        m.communication_number = newest["communication_number"]
        m.communication_type = newest["communication_type"] or m.communication_type
        m.communication_date = newest["communication_date"]
        m.llm_summary = newest["llm_summary"] or m.llm_summary
        m.symptoms = list(newest["symptoms"] or [])
        m.systems = list(newest["systems"] or [])
        m.components = list(newest["components"] or [])
        m.remedy = newest["remedy"] or m.remedy
        m.doc_kind = newest["doc_kind"] or m.doc_kind
    return matches


async def retrieve(
    vehicle_id: int,
    query: str,
    k: int = DEFAULT_K,
    *,
    embedding: Optional[list[float]] = None,
    cost: Optional[QueryCost] = None,
) -> RetrievalResult:
    """Retrieve the top-k communications for a symptom query, scoped to a vehicle.

    ``embedding`` may be supplied by a caller that has already vectorised the
    query (the B2 API embeds once and reuses it); otherwise the query is embedded
    here. ``cost`` accumulates the embedding cost for per-query reporting.
    """
    query = (query or "").strip()
    cost = cost or QueryCost()
    if not query:
        return RetrievalResult(query=query, vehicle_id=vehicle_id, matches=[], cost=cost)

    if embedding is None:
        embedding = await embed_query(query)
        cost.record_embed(query)

    vec_literal = to_pgvector(embedding)
    pool = await get_pool()
    async with pool.acquire() as conn:
        vec_rows = await _vector_candidates(conn, vec_literal, vehicle_id)
        fts_rows = await _fts_candidates(conn, query, vehicle_id)
        matches = _fuse(query, vec_rows, fts_rows, k)
        matches = await _resolve_revision_families(conn, vehicle_id, matches)
    log.info(
        "retrieve vehicle=%s k=%s -> %d matches (vec=%d fts=%d) top=%.4f",
        vehicle_id,
        k,
        len(matches),
        len(vec_rows),
        len(fts_rows),
        matches[0].score if matches else 0.0,
    )
    return RetrievalResult(query=query, vehicle_id=vehicle_id, matches=matches, cost=cost)
