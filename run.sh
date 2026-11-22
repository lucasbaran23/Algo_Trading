#!/usr/bin/env bash
# Start the terminal at http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")/backend"
exec python -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
