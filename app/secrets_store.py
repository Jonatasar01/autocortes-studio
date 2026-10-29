"""Chaves de API coladas na tela (data/secrets.json).

Ficam fora de settings.json, que é exportável e nunca guarda segredos, e dentro de data/, que o git ignora.
A chave nunca volta pela API: a tela só recebe se há chave salva e os quatro últimos caracteres.
A variável de ambiente (GEMINI_API_KEY) continua valendo quando não há chave salva.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading

from . import config
from .errors import AppError

_lock = threading.Lock()
NAMES = ('gemini',)


def _path():
    return config.DATA_DIR / 'secrets.json'


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get(name: str) -> str:
    value = _read().get(name)
    return value.strip() if isinstance(value, str) else ''


def hint(name: str) -> str | None:
    """Os quatro últimos caracteres, para a tela mostrar qual chave está salva."""
    value = get(name)
    return value[-4:] if value else None


def put(name: str, value: str | None) -> None:
    """Salva a chave; vazio remove."""
    if name not in NAMES:
        raise AppError('Chave desconhecida.', 400, 'bad_settings')
    value = (value or '').strip()
    if value and not (20 <= len(value) <= 200 and all(c.isprintable() and not c.isspace() for c in value)):
        raise AppError('Chave inválida: cole a chave inteira, sem espaços.', 400, 'bad_settings')
    with _lock:
        data = _read()
        if value:
            data[name] = value
        else:
            data.pop(name, None)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.secrets-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)   # só o usuário lê (no Linux/Docker; no Windows vale a permissão da pasta)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
