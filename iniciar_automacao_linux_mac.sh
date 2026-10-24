#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --locked --extra ai --extra links
echo "Abra http://127.0.0.1:${PORT:-8000}"
export AUTOCORTES_PORT="${AUTOCORTES_PORT:-${PORT:-8000}}"
exec uv run --no-sync python -m app
