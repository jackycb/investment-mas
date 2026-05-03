#!/usr/bin/env bash
# Start the investment-mas server using the project virtualenv.
# Usage: ./start_server.sh [port]

set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8001}"
exec .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port "$PORT"
