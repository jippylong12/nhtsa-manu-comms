"""The app does not need MongoDB, and cannot quietly start needing it again.

Proving "it works with mongod stopped" would only show that nothing happened to
connect during that run. This is stricter: `motor`, `pymongo` and `bson` are made
unimportable, and then the whole application is imported and every route
inspected. If any module reached for one of them at import time, this fails.

A regression guard as much as a proof. The two-store split took a project to
unpick, and the cheapest way to stop a third store appearing by accident is to
make the driver impossible to import.

Each check runs in a fresh interpreter. Doing it in-process would mean evicting
`src.*` from `sys.modules` and re-importing, which orphans the live asyncpg pool:
the conftest fixture would then close a brand new `src.db` module while the old
pool's connections stayed open, leaking them until the database refused more.
That is not hypothetical; it is what the first version of this file did, and it
took twelve unrelated tests down with it.
"""

import pathlib
import subprocess
import sys
import textwrap

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]

BLOCKER = """
    import sys

    FORBIDDEN = ("motor", "pymongo", "bson")

    class Blocker:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in FORBIDDEN:
                raise ImportError(fullname + " is not available: this app is Postgres only")
            return None

    for name in [n for n in list(sys.modules) if n.split(".")[0] in FORBIDDEN]:
        del sys.modules[name]
    sys.meta_path.insert(0, Blocker())
"""


def run_without_mongo_drivers(body: str) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter where the Mongo drivers cannot import."""
    script = textwrap.dedent(BLOCKER) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_drivers_really_are_blocked():
    """Guard the guard: if the blocker stopped working, everything below would
    pass for the wrong reason."""
    result = run_without_mongo_drivers(
        """
        for name in ("motor", "pymongo", "bson"):
            try:
                __import__(name)
            except ImportError:
                continue
            raise SystemExit("blocker failed for " + name)
        print("blocked")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "blocked" in result.stdout


def test_the_application_imports_without_the_mongo_drivers():
    """Every router, service and job, reached through the app module."""
    result = run_without_mongo_drivers(
        """
        import src.main
        assert src.main.app is not None
        print("imported")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_every_route_is_still_mounted():
    """Importing is not enough; the surface has to be intact."""
    result = run_without_mongo_drivers(
        """
        import src.main
        print("\\n".join(sorted({r.path for r in src.main.app.routes})))
        """
    )
    assert result.returncode == 0, result.stderr
    paths = set(result.stdout.split())

    for expected in (
        "/api/health",
        "/api/vehicles",
        "/api/vehicles/{vehicle_id}",
        "/api/corpus/communications",
        "/api/corpus/communications/{nhtsa_id}",
        "/api/corpus/tags",
        "/api/refresh/{vehicle_id}",
        "/api/refresh/{vehicle_id}/preview",
        "/api/communications/discovery/years",
        "/api/communications/discovery/makes",
        "/api/communications/discovery/models",
        "/api/symptom-search",
        "/api/admin/usage",
    ):
        assert expected in paths, f"{expected} is not mounted"


def test_the_retired_endpoints_are_gone():
    """The Mongo-backed second path stays deleted."""
    result = run_without_mongo_drivers(
        """
        import src.main
        print("\\n".join(sorted({r.path for r in src.main.app.routes})))
        """
    )
    assert result.returncode == 0, result.stderr
    paths = set(result.stdout.split())

    for gone in (
        "/api/communications",
        "/api/communications/{nhtsa_id}",
        "/api/communications/fetch",
        "/api/communications/fetch-sync",
        "/api/communications/stats/{vehicle_id}",
        # The byYmmt resolve step, removed by the plain-nameplate re-key (M3).
        "/api/communications/discovery/variants",
        "/api/communications/discovery/trims",
    ):
        assert gone not in paths, f"{gone} came back"


def test_the_pipeline_jobs_import_without_the_mongo_drivers():
    """The CLI jobs are not reached through src.main, so check them directly.
    `sync` in particular used to carry a Mongo bootstrap."""
    result = run_without_mongo_drivers(
        """
        import importlib
        for module in (
            "src.jobs.sync",
            "src.jobs.process",
            "src.jobs.run_pipeline",
            "src.jobs.digest",
            "src.migrations",
            "src.communications.flatfile",
            "src.db.migrate",
            "src.anthropic_client",
        ):
            importlib.import_module(module)
        print("imported")
        """
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_motor_is_not_a_declared_dependency():
    """A fresh clone must not install it. The import blocking above would still
    pass if the package were merely present and unused."""
    tomllib = pytest.importorskip("tomllib")

    with (BACKEND / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)

    declared = data["project"]["dependencies"] + [
        d for group in data["project"].get("optional-dependencies", {}).values() for d in group
    ]
    for name in ("motor", "pymongo"):
        assert not any(d.lower().startswith(name) for d in declared), f"{name} is still declared"
