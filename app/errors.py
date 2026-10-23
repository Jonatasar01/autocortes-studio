"""Erros de domínio com mensagens seguras para o usuário."""
from __future__ import annotations

import re
import os

from . import config


class AppError(Exception):
    """Erro esperado: a mensagem é exibida ao usuário como está."""

    def __init__(self, message: str, status: int = 400, code: str = 'error', details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details or {}


class Cancelled(Exception):
    """A tarefa foi cancelada pelo usuário."""


def sanitize(text: object, limit: int = 600) -> str:
    """Remove caminhos locais e possíveis segredos de mensagens técnicas."""
    value = str(text or '')
    for name in ('OPENAI_API_KEY', 'ACRCLOUD_ACCESS_KEY', 'ACRCLOUD_ACCESS_SECRET'):
        secret = os.getenv(name, '')
        if secret:
            value = value.replace(secret, '***')
    for path in {str(config.DATA_DIR), str(config.ROOT)}:
        value = value.replace(path, '…')
    value = re.sub(r'(?i)(access_key|secret|signature|token|password)\s*[=:]\s*\S+', r'\1=***', value)
    value = re.sub(r'\bsk-[A-Za-z0-9_-]+', '***', value)
    value = re.sub(r'[A-Za-z]:[\\/][^\r\n\"<>|]*', '…', value)
    value = re.sub(r'(?<![:\w])/(?:home|Users|tmp|var|opt|mnt)/[^\s\"\']+', '…', value)
    value = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', ' ', value)
    return value.strip()[:limit]
