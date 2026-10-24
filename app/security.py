"""Proteções da interface web local (middleware ASGI puro, sem dependência do FastAPI).

- Host permitido: bloqueia DNS rebinding (um site externo resolvendo para 127.0.0.1).
- Requisições que alteram dados exigem o cabeçalho ``X-AutoCortes: 1`` e Origin local:
  impede que páginas de terceiros abertas no navegador enviem uploads/exportações (CSRF).
- Limite de corpo verificado antes da leitura (Content-Length).
- Cabeçalhos de segurança (CSP restritiva, nosniff, sem iframe, sem referrer).
- Senha opcional (``AUTOCORTES_PASSWORD``) via HTTP Basic, com bloqueio temporário após falhas seguidas.
  O navegador pede a senha uma vez e a reenvia sozinho nas chamadas da interface.
- Hosts aceitos podem incluir sufixos (``.trycloudflare.com``) para túneis com endereço sorteado.
- Atrás do Cloudflare Tunnel toda conexão chega de 127.0.0.1; o bloqueio por falhas usa então o IP real
  informado pelo ``cloudflared`` (``CF-Connecting-IP``), para um visitante não bloquear o dono.
"""
from __future__ import annotations

import base64
import hmac
import json
import threading
import time
from urllib.parse import urlsplit

from . import config

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
LOOPBACK_CLIENTS = {'127.0.0.1', '::1'}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")


def _hostname(host: str) -> str:
    host = (host or '').strip().lower()
    if host.startswith('['):
        return host[:host.find(']') + 1] if ']' in host else host
    return host.split(':', 1)[0]


def _host_allowed(hostname: str, hosts: set[str]) -> bool:
    """Host exato da lista ou subdomínio de uma entrada iniciada por ponto (ex.: ``.trycloudflare.com``)."""
    if hostname in hosts:
        return True
    return any(h.startswith('.') and len(h) > 1 and hostname.endswith(h) and len(hostname) > len(h) for h in hosts)


def _client_id(scope, headers: dict[str, str]) -> str:
    client = (scope.get('client') or ('?', 0))[0]
    forwarded = headers.get('cf-connecting-ip', '').strip()
    # Só o cloudflared (rodando neste PC) chega por loopback; de outro aparelho o cabeçalho é ignorado.
    if client in LOOPBACK_CLIENTS and forwarded:
        return forwarded[:64]
    return client


def _password_ok(header: str, password: str) -> bool:
    scheme, _, value = (header or '').partition(' ')
    if scheme.lower() != 'basic':
        return False
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return False
    given = decoded.partition(':')[2]  # o usuário é ignorado
    return hmac.compare_digest(given.encode('utf-8'), password.encode('utf-8'))


class LocalSecurityMiddleware:
    def __init__(self, app, allowed_hosts: set[str] | None = None, max_body: int | None = None,
                 password: str | None = None):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.max_body = max_body
        self.password_override = password
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def password(self) -> str:
        return self.password_override if self.password_override is not None else config.PASSWORD

    def _locked_out(self, client: str, now: float) -> bool:
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < config.AUTH_LOCKOUT_SECONDS]
            if recent:
                self._failures[client] = recent
            else:
                self._failures.pop(client, None)
            return len(recent) >= config.AUTH_MAX_FAILURES

    def _register_failure(self, client: str, now: float) -> None:
        with self._lock:
            self._failures.setdefault(client, []).append(now)
            if len(self._failures) > 10000:  # evita crescer sem limite
                self._failures.clear()

    @property
    def hosts(self) -> set[str]:
        return self.allowed_hosts if self.allowed_hosts is not None else config.ALLOWED_HOSTS

    @property
    def body_limit(self) -> int:
        return self.max_body if self.max_body is not None else config.MAX_UPLOAD_BYTES + 16 * 1024 ** 2

    async def _reject(self, send, status: int, message: str, extra_headers=(), code: str = 'blocked') -> None:
        body = json.dumps({'detail': message, 'code': code}, ensure_ascii=False).encode('utf-8')
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'application/json; charset=utf-8'),
                                (b'content-length', str(len(body)).encode()),
                                (b'x-content-type-options', b'nosniff'), (b'cache-control', b'no-store'),
                                *extra_headers]})
        await send({'type': 'http.response.body', 'body': body})

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers', [])}
        if '*' not in self.hosts and not _host_allowed(_hostname(headers.get('host', '')), self.hosts):
            return await self._reject(send, 400, 'Host não permitido. Acesse por http://127.0.0.1:8000 '
                                      'ou inclua este endereço em AUTOCORTES_ALLOWED_HOSTS.')
        if self.password:
            client = _client_id(scope, headers)
            now = time.monotonic()
            if self._locked_out(client, now):
                return await self._reject(send, 429, 'Muitas tentativas de senha. Aguarde alguns minutos.',
                                          code='auth_locked')
            if not _password_ok(headers.get('authorization', ''), self.password):
                if headers.get('authorization'):
                    self._register_failure(client, now)
                return await self._reject(send, 401, 'Senha necessária.', code='auth_required',
                                          extra_headers=[(b'www-authenticate',
                                                          b'Basic realm="AutoCortes", charset="UTF-8"')])
        if scope['method'] not in SAFE_METHODS:
            origin = headers.get('origin')
            if origin is not None:
                try:
                    parsed = urlsplit(origin)
                    valid_origin = (parsed.scheme in ('http', 'https') and not parsed.username
                                    and not parsed.password and not parsed.path and not parsed.query
                                    and not parsed.fragment
                                    and (_host_allowed(_hostname(parsed.netloc), self.hosts)
                                         or ('*' in self.hosts and _hostname(parsed.netloc)
                                             == _hostname(headers.get('host', '')))))
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
