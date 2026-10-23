"""Proteções da interface web local (middleware ASGI puro, sem dependência do FastAPI).

- Host permitido: bloqueia DNS rebinding (um site externo resolvendo para 127.0.0.1).
- Requisições que alteram dados exigem o cabeçalho ``X-AutoCortes: 1`` e Origin local:
  impede que páginas de terceiros abertas no navegador enviem uploads/exportações (CSRF).
- Limite de corpo verificado antes da leitura (Content-Length).
- Cabeçalhos de segurança (CSP restritiva, nosniff, sem iframe, sem referrer).
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from . import config

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")


def _hostname(host: str) -> str:
    host = (host or '').strip().lower()
    if host.startswith('['):
        return host[:host.find(']') + 1] if ']' in host else host
    return host.split(':', 1)[0]


class LocalSecurityMiddleware:
    def __init__(self, app, allowed_hosts: set[str] | None = None, max_body: int | None = None):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.max_body = max_body

    @property
    def hosts(self) -> set[str]:
        return self.allowed_hosts if self.allowed_hosts is not None else config.ALLOWED_HOSTS

    @property
    def body_limit(self) -> int:
        return self.max_body if self.max_body is not None else config.MAX_UPLOAD_BYTES + 16 * 1024 ** 2

    async def _reject(self, send, status: int, message: str) -> None:
        body = json.dumps({'detail': message, 'code': 'blocked'}, ensure_ascii=False).encode('utf-8')
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'application/json; charset=utf-8'),
                                (b'content-length', str(len(body)).encode()),
                                (b'x-content-type-options', b'nosniff')]})
        await send({'type': 'http.response.body', 'body': body})

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        if _hostname(headers.get('host', '')) not in self.hosts:
            return await self._reject(send, 400, 'Host não permitido. Acesse por http://127.0.0.1:8000.')
        if scope['method'] not in SAFE_METHODS:
            origin = headers.get('origin')
            if origin is not None:
                try:
                    parsed = urlsplit(origin)
                    valid_origin = (parsed.scheme in ('http', 'https') and not parsed.username
                                    and not parsed.password and not parsed.path and not parsed.query
                                    and not parsed.fragment and _hostname(parsed.netloc) in self.hosts)
                    parsed.port  # rejeita portas malformadas
                except ValueError:
                    valid_origin = False
                if not valid_origin:
                    return await self._reject(send, 403, 'Origem não permitida.')
            if headers.get('sec-fetch-site') == 'cross-site':
                return await self._reject(send, 403, 'Requisição de outro site bloqueada.')
            if headers.get(config.CSRF_HEADER) != '1':
                return await self._reject(send, 403, 'Requisição bloqueada: cabeçalho de segurança ausente.')
            try:
                length = int(headers.get('content-length') or 0)
            except ValueError:
                return await self._reject(send, 400, 'Content-Length inválido.')
            if length < 0:
                return await self._reject(send, 400, 'Content-Length inválido.')
            if length > self.body_limit:
                return await self._reject(send, 413, 'Arquivo maior que o limite configurado.')
        path = scope.get('path', '')

        async def send_with_headers(message):
            if message['type'] == 'http.response.start':
                extra = [(b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'),
                         (b'referrer-policy', b'no-referrer'),
                         (b'cross-origin-opener-policy', b'same-origin'),
                         (b'cross-origin-resource-policy', b'same-origin')]
                if not path.startswith('/api/docs'):
                    extra.append((b'content-security-policy', CSP.encode()))
                if path.startswith('/api/'):
                    extra.append((b'cache-control', b'no-store'))
                existing = {k.lower() for k, _ in message.get('headers', [])}
                message = {**message, 'headers': list(message.get('headers', [])) +
                           [(k, v) for k, v in extra if k not in existing]}
            await send(message)

        return await self.app(scope, receive, send_with_headers)
