"""backend/start.sh: migrate first, fail closed, bind the platform's port (JIP-1595)."""

import os
import pathlib
import subprocess

START_SH = pathlib.Path(__file__).resolve().parents[1] / "start.sh"


def _run(tmp_path, *, migrate_exit: int, port: str | None):
    """Run start.sh with stub `python` and `uvicorn` that log their arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name, code in (("python", migrate_exit), ("uvicorn", 0)):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit {code}\n')
        os.chmod(stub, 0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    if port is not None:
        env["PORT"] = port
    result = subprocess.run(["sh", str(START_SH)], env=env, capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return result.returncode, calls


def test_binds_the_injected_port(tmp_path):
    code, calls = _run(tmp_path, migrate_exit=0, port="8123")
    assert code == 0
    assert calls == [
        "python -m src.db.migrate",
        "uvicorn src.main:app --host 0.0.0.0 --port 8123",
    ]


def test_defaults_to_port_8000(tmp_path):
    code, calls = _run(tmp_path, migrate_exit=0, port=None)
    assert code == 0
    assert calls[-1] == "uvicorn src.main:app --host 0.0.0.0 --port 8000"


def test_a_failed_migration_never_starts_the_api(tmp_path):
    code, calls = _run(tmp_path, migrate_exit=3, port="8123")
    assert code != 0
    assert calls == ["python -m src.db.migrate"]
