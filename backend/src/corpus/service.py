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
    if systems:
        f.add(
            "EXISTS (SELECT 1 FROM comm_documents d WHERE d.communication_id = c.id "
            "AND d.systems && {}::text[])",
            systems,
        )
    if components:
        f.add(
            "EXISTS (SELECT 1 FROM comm_documents d WHERE d.communication_id = c.id "
            "AND d.components && {}::text[])",
            components,
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
                c.communication_date, c.summary, c.status,
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

        result = []
        for r in rows:
            d = dict(r)
            d["vehicles"] = await _vehicles_for(conn, r["nhtsa_id"])
            result.append(d)

    return result, total


async def _vehicles_for(conn, nhtsa_id: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT v.nhtsa_vehicle_id, v.year, v.make, v.model, v.trim
        FROM communication_vehicles cv
        JOIN vehicles v ON v.id = cv.vehicle_id
        JOIN communications c ON c.id = cv.communication_id
        WHERE c.nhtsa_id = $1
        ORDER BY v.year, v.model
        """,
        nhtsa_id,
    )
    return [dict(r) for r in rows]


async def get_communication(nhtsa_id: str) -> Optional[dict]:
    """Return a single communication with its full document list, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        comm = await conn.fetchrow(
            """
            SELECT nhtsa_id, communication_number, communication_type,
                   communication_date, summary, details_summary, status,
                   status_reason, processed_at, id
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
    """Distinct systems and components with document counts, for filter UIs."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        systems = await conn.fetch(
            """
            SELECT s AS tag, count(*) AS count
            FROM comm_documents d, unnest(d.systems) s
            GROUP BY s ORDER BY count DESC, tag LIMIT $1
            """,
            limit,
        )
        components = await conn.fetch(
            """
            SELECT s AS tag, count(*) AS count
            FROM comm_documents d, unnest(d.components) s
            GROUP BY s ORDER BY count DESC, tag LIMIT $1
            """,
            limit,
        )
    return {
        "systems": [dict(r) for r in systems],
        "components": [dict(r) for r in components],
    }
