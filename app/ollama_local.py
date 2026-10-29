"""Cliente do Ollama local (http://127.0.0.1:11434), usado pela curadoria e pelo refinador do ranking.

Só a biblioteca padrão. A URL é fixa no próprio computador e a conexão ignora proxies do sistema: nenhum texto da
transcrição sai da máquina por aqui. Erros viram ``AppError`` com mensagem curta, sem detalhes internos.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from . import config
from .errors import AppError, sanitize

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_status_lock = threading.Lock()
_status_cache: tuple[float, dict] | None = None
STATUS_TTL = 15.0


def request(path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(config.OLLAMA_URL + path, data=body, headers={'Content-Type': 'application/json'})
    try:
        with _opener.open(req, timeout=timeout or config.OLLAMA_TIMEOUT) as response:
            raw = response.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise AppError(f'O Ollama local recusou o pedido ({exc.code}).', 502, 'ollama_http') from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AppError('Ollama local indisponível em 127.0.0.1:11434.', 503, 'ollama_unavailable',
                       {'technical': sanitize(type(exc).__name__, 60)}) from None
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AppError('Resposta inválida do Ollama local.', 502, 'ollama_bad_response') from None
    if not isinstance(data, dict):
        raise AppError('Resposta inválida do Ollama local.', 502, 'ollama_bad_response')
    return data


def installed_models(timeout: float = 3) -> set[str]:
    data = request('/api/tags', timeout=timeout)
    models = data.get('models')
    if not isinstance(models, list):
        raise AppError('Resposta inválida do Ollama local.', 502, 'ollama_bad_response')
    return {str(m.get('name', '')) for m in models if isinstance(m, dict)}


def _has(names: set[str], model: str) -> bool:
    return model in names or f'{model}:latest' in names


def pick_model(names: set[str]) -> str | None:
    """O modelo principal se estiver instalado; senão o reserva; senão ``None``."""
    for model in dict.fromkeys(m for m in (config.OLLAMA_MODEL, config.OLLAMA_FALLBACK_MODEL) if m):
        if _has(names, model):
            return model
    return None


def status(refresh: bool = False) -> dict:
    """Ollama no ar e modelo disponível (em cache por alguns segundos: a tela consulta com frequência)."""
    global _status_cache
    with _status_lock:
        if not refresh and _status_cache and time.monotonic() - _status_cache[0] < STATUS_TTL:
            return dict(_status_cache[1])
    try:
        names = installed_models(timeout=1.5)
        model = pick_model(names)
        info = {'running': True, 'model': model, 'models': sorted(n for n in names if n)[:20],
                'fallback': bool(model) and model != config.OLLAMA_MODEL,
                'message': (f'Ollama local pronto ({model}).' if model else
                            f'Ollama no ar, mas sem o modelo: rode "ollama pull {config.OLLAMA_MODEL}".')}
    except AppError as exc:
        info = {'running': False, 'model': None, 'models': [], 'fallback': False,
                'message': exc.message + ' Instale e abra o Ollama (ollama.com).'}
    with _status_lock:
        _status_cache = (time.monotonic(), info)
    return dict(info)


def generate(*, system: str, prompt: str, schema: dict, model: str, timeout: float | None = None) -> dict:
    """Uma geração com saída estruturada (JSON Schema no campo ``format``), temperatura 0 e sem "pensar"."""
    payload = {'model': model, 'system': system, 'prompt': prompt, 'stream': False, 'format': schema,
               'think': False, 'keep_alive': config.OLLAMA_KEEP_ALIVE,
               'options': {'temperature': 0, 'num_ctx': config.OLLAMA_NUM_CTX}}
    data = request('/api/generate', payload, timeout=timeout or config.OLLAMA_CURATION_TIMEOUT)
    text = data.get('response')
    if not isinstance(text, str):
        raise AppError('O Ollama não devolveu texto.', 502, 'ollama_bad_response')
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise AppError('O Ollama devolveu JSON inválido.', 502, 'ollama_bad_response') from None
    if not isinstance(parsed, dict):
        raise AppError('O Ollama devolveu JSON inválido.', 502, 'ollama_bad_response')
    return parsed
