"""docs/schema.md names every table the project's migrations leave in place (JIP-1596).

Reads files only; no database.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "backend" / "migrations"
SCHEMA_DOC = ROOT / "docs" / "schema.md"

# The product project's migrations start here (0014_accounts.sql).
FIRST_PRODUCT_MIGRATION = "0014_accounts.sql"

_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?(\w+)", re.I)
_DROP = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?(\w+)", re.I)


def _product_tables() -> set[str]:
    created: set[str] = set()
    dropped: set[str] = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name < FIRST_PRODUCT_MIGRATION:
            continue
        sql = path.read_text()
        created.update(_CREATE.findall(sql))
        dropped.update(_DROP.findall(sql))
    return created - dropped


def test_the_product_migrations_create_tables():
    assert {"users", "user_vehicles", "entitlements", "stripe_events", "alert_state"} <= (
        _product_tables()
    )


def test_every_product_table_appears_in_schema_doc():
    doc = SCHEMA_DOC.read_text()
    missing = sorted(
        t for t in _product_tables() if not re.search(rf"\b{re.escape(t)}\b", doc)
    )
    assert not missing, f"docs/schema.md does not mention: {missing}"
