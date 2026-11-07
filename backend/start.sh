#!/bin/sh
# Apply pending migrations, then run the API. Used as the image CMD of the api service.
set -e
python -m src.db.migrate
exec uvicorn src.main:app --host 0.0.0.0 --port 8000
