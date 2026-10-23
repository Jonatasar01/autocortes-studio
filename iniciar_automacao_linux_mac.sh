#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --locked --extra ai --extra links
echo "Abra http://127.0.0.1:${PORT:-8000}"
exec uv run --no-sync python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
