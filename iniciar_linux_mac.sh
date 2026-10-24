#!/usr/bin/env bash
# Inicia o AutoCortes em 127.0.0.1 (não exposto na rede).
# Rede local: AUTOCORTES_HOST=0.0.0.0 AUTOCORTES_PASSWORD=sua-senha ./iniciar_linux_mac.sh
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then echo "Python 3.10+ não encontrado."; exit 1; fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "É necessário Python 3.10 ou superior."; exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "AVISO: ffmpeg/ffprobe não encontrados no PATH. Instale o FFmpeg (ex.: brew install ffmpeg / sudo apt install ffmpeg)."
fi
if [ ! -f .venv/.deps-ok ]; then
  [ -x .venv/bin/python ] || "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.deps-ok
fi
export AUTOCORTES_PORT="${AUTOCORTES_PORT:-${PORT:-8000}}"
URL="http://127.0.0.1:${AUTOCORTES_PORT}"
( sleep 2; (command -v xdg-open >/dev/null && xdg-open "$URL") || (command -v open >/dev/null && open "$URL") ) >/dev/null 2>&1 &
exec .venv/bin/python -m app
