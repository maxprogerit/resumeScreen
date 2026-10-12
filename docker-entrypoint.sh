#!/bin/sh
set -e
alembic upgrade head
if [ "${SEED_DEMO:-0}" = "1" ]; then
    python -m scripts.seed_demo --count "${SEED_DEMO_COUNT:-25}"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
