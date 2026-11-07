#!/bin/sh
# Apply pending migrations, then run the API. Used as the image CMD of the api service.
# Binds $PORT when the platform injects one (Railway health-checks that port), else 8000.
set -e
python -m src.db.migrate
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"
