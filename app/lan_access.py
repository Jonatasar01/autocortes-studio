"""Senha para abrir o AutoCortes pela rede (celular, outro computador no mesmo Wi-Fi).

Neste computador (127.0.0.1) nunca há senha. De outro aparelho, com a senha definida em Configurações, o navegador
pede usuário e senha (HTTP Basic; o usuário pode ser qualquer um). Só o hash PBKDF2 fica em ``data/secrets.json``.
Sem senha definida, a rede continua aberta como antes (o servidor só aceita a rede com AUTOCORTES_ALLOWED_HOSTS).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import threading

from . import secrets_store
from .errors import AppError

NAME = 'lan_password'
ITERATIONS = 200_000
LOOPBACK = {'127.0.0.1', '::1', 'localhost'}
MIN_LEN, MAX_LEN = 6, 128

_lock = threading.Lock()
_accepted: dict[str, str] = {}   # sha256 do cabeçalho aceito -> hash da senha em vigor (evita PBKDF2 a cada pedido)


def _digest(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)


def enabled() -> bool:
    return bool(secrets_store.get(NAME))


def set_password(password: str | None) -> None:
    """Define a senha da rede; vazia remove."""
    password = password or ''
    if password and not (MIN_LEN <= len(password) <= MAX_LEN):
        raise AppError(f'Use uma senha de {MIN_LEN} a {MAX_LEN} caracteres.', 400, 'bad_input')
    if password:
        salt = secrets.token_bytes(16)
        value = f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${_digest(password, salt, ITERATIONS).hex()}'
    else:
        value = ''
    secrets_store.put(NAME, value)
    with _lock:
        _accepted.clear()


def check(password: str) -> bool:
    stored = secrets_store.get(NAME)
    try:
        _, iterations, salt, expected = stored.split('$')
        return hmac.compare_digest(_digest(password, bytes.fromhex(salt), int(iterations)).hex(), expected)
    except ValueError:
        return False


def allowed(client_host: str | None, authorization: str | None) -> bool:
    """Pedido liberado? Neste computador, sempre; de fora, com a senha certa (ou sem senha definida)."""
    stored = secrets_store.get(NAME)
    if not stored or (client_host or '') in LOOPBACK:
        return True
    if not authorization or not authorization.lower().startswith('basic '):
        return False
    key = hashlib.sha256(authorization.encode('utf-8', 'replace')).hexdigest()
    with _lock:
        if _accepted.get(key) == stored:
            return True
    try:
        decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, _, password = decoded.partition(':')
    if not check(password):
        return False
    with _lock:
        if len(_accepted) > 64:
            _accepted.clear()
        _accepted[key] = stored
    return True
