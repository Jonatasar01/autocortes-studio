"""Configuração central lida de variáveis de ambiente.

Todos os valores possuem padrões seguros para uso pessoal em 127.0.0.1.
Os módulos devem ler ``config.X`` em tempo de execução (não ``from config import X``)
para que os testes possam ajustar valores.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2.4.0'


def _int(name: str, default: int, low: int | None = None, high: int | None = None) -> int:
    try:
        value = int(float(os.getenv(name, str(default))))
    except ValueError:
        value = default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, '1' if default else '0').strip().lower() in {'1', 'true', 'yes', 'sim', 'on'}


DATA_DIR = Path(os.getenv('AUTOCORTES_DATA', str(ROOT / 'data'))).resolve()
STATIC_DIR = ROOT / 'app' / 'static'

# Limites de entrada
MAX_UPLOAD_BYTES = int(float(os.getenv('MAX_UPLOAD_GB', '4')) * 1024 ** 3)
MAX_SOURCE_SECONDS = _int('AUTOCORTES_MAX_SOURCE_HOURS', 6, 1, 48) * 3600
MAX_DIMENSION = _int('AUTOCORTES_MAX_DIMENSION', 8192, 640, 16384)
MIN_FREE_BYTES = _int('AUTOCORTES_MIN_FREE_MB', 1024, 100) * 1024 ** 2
MAX_CLIP_SECONDS = 900

# Fila e recursos
ANALYSIS_WORKERS = _int('AUTOCORTES_ANALYSIS_WORKERS', 1, 1, 4)
EXPORT_WORKERS = _int('AUTOCORTES_EXPORT_WORKERS', 1, 1, 4)
FFMPEG_THREADS = _int('AUTOCORTES_FFMPEG_THREADS', 0, 0, 64)  # 0 = automático
FFMPEG_PRESET = os.getenv('AUTOCORTES_X264_PRESET', 'veryfast')
LOW_PRIORITY = _flag('AUTOCORTES_LOW_PRIORITY', True)

# IA local
WHISPER_MODEL = os.getenv('WHISPER_MODEL', 'small')
WHISPER_DEVICE = os.getenv('WHISPER_DEVICE', 'auto')
WHISPER_LANGUAGE = os.getenv('WHISPER_LANGUAGE', 'pt')  # 'auto' para detecção automática
# Legenda de cada corte retranscrita com um modelo maior (só com GPU); 'off' desliga.
WHISPER_REFINE_MODEL = os.getenv('WHISPER_REFINE_MODEL', 'large-v3-turbo')
OLLAMA_ENABLED = _flag('OLLAMA_ENABLED', False)
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'llama3.2')
OLLAMA_URL = 'http://127.0.0.1:11434'  # fixo: nunca envia transcrições para fora da máquina
OLLAMA_TIMEOUT = _int('OLLAMA_TIMEOUT', 90, 5, 600)
OLLAMA_TOTAL_TIMEOUT = _int('OLLAMA_TOTAL_TIMEOUT', 600, 30, 3600)

# Reconhecimento musical externo (opt-in)
ACRCLOUD_MAX_SAMPLES = _int('ACRCLOUD_MAX_SAMPLES', 3, 1, 10)

# Narração com ChatGPT/OpenAI (opt-in; chave apenas por variável de ambiente OPENAI_API_KEY)
OPENAI_BASE_URL = 'https://api.openai.com/v1'
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-6-luna')
OPENAI_TTS_MODEL = os.getenv('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts')
OPENAI_VOICE = os.getenv('OPENAI_VOICE', 'cedar')
OPENAI_TIMEOUT = _int('OPENAI_TIMEOUT', 120, 10, 600)
OPENAI_MAX_FRAMES = _int('OPENAI_MAX_FRAMES', 8, 0, 20)

# Automação: pastas definidas só por variável de ambiente (a interface não grava em caminhos arbitrários)
WATCH_DIR = Path(os.getenv('AUTOCORTES_WATCH_DIR', str(ROOT / 'entrada'))).resolve()
OUTPUT_DIR = Path(os.getenv('AUTOCORTES_OUTPUT_DIR', str(ROOT / 'saida'))).resolve()
WATCH_INTERVAL = _int('AUTOCORTES_WATCH_INTERVAL', 5, 1, 300)
JOB_MAX_RETRIES = _int('AUTOCORTES_JOB_RETRIES', 2, 0, 5)
JOB_RETRY_BASE_SECONDS = _int('AUTOCORTES_RETRY_SECONDS', 30, 0, 3600)

# Interface/segurança
ENABLE_API_DOCS = _flag('AUTOCORTES_API_DOCS', False)
ALLOWED_HOSTS = {'127.0.0.1', 'localhost', '[::1]', '::1', 'testserver'} | {
    h.strip().lower() for h in os.getenv('AUTOCORTES_ALLOWED_HOSTS', '').split(',') if h.strip()}
CSRF_HEADER = 'x-autocortes'
