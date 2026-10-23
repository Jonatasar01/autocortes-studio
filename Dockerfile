FROM ghcr.io/astral-sh/uv:0.13.5 AS uv
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    AUTOCORTES_DATA=/state/data \
    AUTOCORTES_WATCH_DIR=/state/entrada \
    AUTOCORTES_OUTPUT_DIR=/state/saida \
    HF_HOME=/models \
    WHISPER_DEVICE=cpu \
    AUTOCORTES_FFMPEG_THREADS=2

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 autocortes \
    && useradd --uid 10001 --gid autocortes --create-home autocortes \
    && mkdir -p /state/data /state/entrada /state/saida /models \
    && chown -R autocortes:autocortes /state /models

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra ai --extra music --extra links --python /usr/local/bin/python \
    && uv cache clean
COPY app ./app

USER autocortes
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=15s --start-period=60s --retries=3 \
    CMD python -c "import json, urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=10)); assert r['ok']"
# Um único processo é necessário para a fila SQLite e os locks locais.
# A porta do host fica limitada a loopback pelo Compose.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
