"""Query layer for the Postgres corpus read API.

Hand-written SQL against the canonical schema. Filters are composed positionally
so every value is a bound parameter; no user input is ever interpolated into the
statement text.
"""

from datetime import datetime
from typing import Any, Optional

from src.db import get_pool


class _Filters:
    """Accumulates WHERE clauses and their bound parameters in lockstep."""

    def __init__(self) -> None:
        self.clauses: list[str] = []
        self.params: list[Any] = []

    def add(self, clause_template: str, value: Any) -> None:
        """Add a clause. Use ``{}`` where the ``$n`` placeholder should go."""
        self.params.append(value)
        self.clauses.append(clause_template.format(f"${len(self.params)}"))

    def where(self) -> str:
        return ("WHERE " + " AND ".join(self.clauses)) if self.clauses else ""


def _build_filters(
    *,
    vehicle_id: Optional[int],
    comm_type: Optional[str],
    status: Optional[str],
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    systems: Optional[list[str]],
    components: Optional[list[str]],
    search: Optional[str],
) -> _Filters:
    f = _Filters()

    if vehicle_id is not None:
        # EXISTS against the join table, so a comm shared by several vehicles is
        # still returned once.
        f.add(
            "EXISTS (SELECT 1 FROM communication_vehicles cv "
            "JOIN vehicles v ON v.id = cv.vehicle_id "
            "WHERE cv.communication_id = c.id AND v.nhtsa_vehicle_id = {})",
            vehicle_id,
        )
    if comm_type:
        f.add("c.communication_type = {}", comm_type)
    if status:
        f.add("c.status = {}", status)
    if date_from:
        f.add("c.communication_date >= {}", date_from)
    if date_to:
        f.add("c.communication_date <= {}", date_to)
    # Tag matching is case-insensitive: the LLM emits both "electrical" and
    # "Electrical", so a case-sensitive `&&` overlap would silently miss half
    # the corpus. The filter value is lowercased and compared against each
    # unnested, lowercased tag.
    if systems:
        f.add(
            "EXISTS (SELECT 1 FROM comm_documents d, unnest(d.systems) s "
            "WHERE d.communication_id = c.id AND lower(s) = ANY({}::text[]))",
            [t.lower() for t in systems],
        )
    if components:
        f.add(
            "EXISTS (SELECT 1 FROM comm_documents d, unnest(d.components) s "
            "WHERE d.communication_id = c.id AND lower(s) = ANY({}::text[]))",
            [t.lower() for t in components],
        )
    if search:
        # Full-text over both the communication's own vector and its documents',
        # replacing the Mongo regex scan. plainto_tsquery keeps user input from
        # ever being parsed as tsquery operators.
        f.add(
            "(c.search_tsv @@ plainto_tsquery('english', {0}) "
            "OR EXISTS (SELECT 1 FROM comm_documents d WHERE d.communication_id = c.id "
            "AND d.search_tsv @@ plainto_tsquery('english', {0})))",
            search,
        )
    return f


async def list_communications(
    *,
    vehicle_id: Optional[int] = None,
    comm_type: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    systems: Optional[list[str]] = None,
    components: Optional[list[str]] = None,
    search: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], int]:
    """Return one page of communications plus the total matching count."""
    f = _build_filters(
        vehicle_id=vehicle_id,
        comm_type=comm_type,
        status=status,
        date_from=date_from,
        date_to=date_to,
        systems=systems,
        components=components,
        search=search,
    )
    where = f.where()
    pool = await get_pool()

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM communications c {where}", *f.params)

        limit_param = f"${len(f.params) + 1}"
        offset_param = f"${len(f.params) + 2}"
        rows = await conn.fetch(
            f"""
            SELECT
                c.nhtsa_id, c.communication_number, c.communication_type,
                c.communication_date, coalesce(c.summary, '') AS summary, c.status,
                (SELECT count(*) FROM comm_documents d WHERE d.communication_id = c.id) AS document_count,
                (SELECT d.llm_summary FROM comm_documents d
                 WHERE d.communication_id = c.id AND d.llm_summary IS NOT NULL
                 ORDER BY d.id LIMIT 1) AS llm_summary,
                (SELECT coalesce(array_agg(DISTINCT s), '{{}}')
                 FROM comm_documents d, unnest(d.symptoms) s
                 WHERE d.communication_id = c.id) AS symptoms,
                (SELECT coalesce(array_agg(DISTINCT s), '{{}}')
                 FROM comm_documents d, unnest(d.systems) s
                 WHERE d.communication_id = c.id) AS systems
            FROM communications c
            {where}
            ORDER BY c.communication_date DESC NULLS LAST, c.id DESC
            LIMIT {limit_param} OFFSET {offset_param}
            """,
            *f.params,
            per_page,
            (page - 1) * per_page,
        )

        # Fetch every page row's vehicles in ONE query and group in Python,
        # rather than a query per row. Over a remote database the per-row
        # version was an N+1 that made a 200-row page take tens of seconds.
        nhtsa_ids = [r["nhtsa_id"] for r in rows]
        vehicles_by_comm = await _vehicles_for_many(conn, nhtsa_ids)

        result = []
        for r in rows:
            d = dict(r)
            d["vehicles"] = vehicles_by_comm.get(r["nhtsa_id"], [])
            result.append(d)

    return result, total


async def _vehicles_for_many(conn, nhtsa_ids: list[str]) -> dict[str, list[dict]]:
    """Vehicles for many communications in a single round trip, keyed by nhtsa_id."""
    if not nhtsa_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT c.nhtsa_id,
               v.nhtsa_vehicle_id, v.year, v.make, v.model, v.trim
        FROM communication_vehicles cv
        JOIN vehicles v ON v.id = cv.vehicle_id
        JOIN communications c ON c.id = cv.communication_id
        WHERE c.nhtsa_id = ANY($1::text[])
        ORDER BY v.year, v.model
        """,
        nhtsa_ids,
    )
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        nid = d.pop("nhtsa_id")
        grouped.setdefault(nid, []).append(d)
    return grouped


async def _vehicles_for(conn, nhtsa_id: str) -> list[dict]:
    """Vehicles for a single communication (used by the detail endpoint)."""
    return (await _vehicles_for_many(conn, [nhtsa_id])).get(nhtsa_id, [])


async def get_communication(nhtsa_id: str) -> Optional[dict]:
    """Return a single communication with its full document list, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        comm = await conn.fetchrow(
            """
            SELECT nhtsa_id, communication_number, communication_type,
                   communication_date, coalesce(summary, '') AS summary,
                   details_summary, status, status_reason, processed_at, id
            FROM communications WHERE nhtsa_id = $1
            """,
            nhtsa_id,
        )
        if comm is None:
            return None

        docs = await conn.fetch(
            """
            SELECT id, url, doc_summary, extraction_method, page_count,
                   llm_summary, doc_kind, symptoms, systems, components,
                   remedy, applicability, (embedding IS NOT NULL) AS has_embedding
            FROM comm_documents
            WHERE communication_id = $1
            ORDER BY id
            """,
            comm["id"],
        )

        result = dict(comm)
        result.pop("id", None)
        result["documents"] = [dict(d) for d in docs]
        result["vehicles"] = await _vehicles_for(conn, nhtsa_id)
        return result


async def tag_vocabulary(limit: int = 100) -> dict[str, list[dict]]:
    """Distinct systems and components with document counts, for filter UIs.

    Case variants are merged (the LLM emits both "electrical" and "Electrical"),
    counts summed, and the most frequent surface form kept as the label. This
    matches the case-insensitive tag filter, so a chip's count reflects exactly
    what clicking it returns.
    """
    # `mode()` picks the most common original casing within each lowercased group.
    tag_sql = """
        SELECT label AS tag, total AS count FROM (
            SELECT lower(s) AS key,
                   mode() WITHIN GROUP (ORDER BY s) AS label,
                   count(*) AS total
            FROM comm_documents d, unnest(d.{col}) s
            GROUP BY lower(s)
        ) g
        ORDER BY total DESC, tag
        LIMIT $1
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        systems = await conn.fetch(tag_sql.format(col="systems"), limit)
        components = await conn.fetch(tag_sql.format(col="components"), limit)
    return {
        "systems": [dict(r) for r in systems],
        "components": [dict(r) for r in components],
    }
