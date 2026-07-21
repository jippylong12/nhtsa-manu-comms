#!/usr/bin/env bash
#
# Start the backend API.
#
# Use this rather than a bare `uvicorn src.main:app`. `src` is a very generic
# top-level package name, and an editable install of another project can put its
# own `src` on sys.path for every interpreter on this machine. When that happens,
# running uvicorn from anywhere except backend/ silently starts the *other*
# project's app: the server boots cleanly, logs a normal startup, and then 404s
# every route, which reads like the database has been wiped.
#
# Two things prevent that here: --app-dir pins the import to this repo, and the
# preflight below refuses to bind a port if anything still shadows it.
#
# Usage:
#   scripts/dev-api.sh              # 127.0.0.1:8000
#   scripts/dev-api.sh --reload     # extra args pass through to uvicorn
#   API_PORT=8001 scripts/dev-api.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT/backend"
PYTHON="${PYTHON:-python3}"
HOST="${API_HOST:-127.0.0.1}"
PORT="${API_PORT:-8000}"

# Preflight: prove `src` resolves inside this repo before we bind anything.
"$PYTHON" - "$APP_DIR" <<'PY'
import pathlib
import sys

app_dir = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(app_dir))

import src

resolved = pathlib.Path(src.__file__).resolve()
if app_dir not in resolved.parents:
    sys.exit(
        "refusing to start: the 'src' package resolves outside this repo.\n"
        f"  resolved to : {resolved}\n"
        f"  expected in : {app_dir}\n"
        "Another project has installed a top-level 'src' package. Uninstall it, "
        "or run this script with a PYTHON that does not see it."
    )
PY

# cd as well as --app-dir: pydantic-settings reads .env relative to the cwd.
cd "$APP_DIR"
exec "$PYTHON" -m uvicorn src.main:app \
    --app-dir "$APP_DIR" \
    --host "$HOST" \
    --port "$PORT" \
    "$@"
