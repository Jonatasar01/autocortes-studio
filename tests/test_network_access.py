"""Acesso pela rede: senha (HTTP Basic), bloqueio após falhas, Host curinga e recusa sem senha."""
from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
import unittest
from unittest import mock

from app import config
from app.security import LocalSecurityMiddleware


def basic(password: str, user: str = 'eu') -> str:
    return 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()


class PasswordTests(unittest.TestCase):
    def setUp(self):
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})
        self.mw = LocalSecurityMiddleware(app, allowed_hosts={'127.0.0.1', '192.168.0.10'}, max_body=1000,
                                          password='segredo')

    def call(self, method='GET', headers=None, client='192.168.0.20'):
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {'type': 'http.request', 'body': b''}
        hdrs = [(k.encode(), v.encode()) for k, v in {'host': '192.168.0.10:8000', **(headers or {})}.items()]
        scope = {'type': 'http', 'method': method, 'path': '/', 'headers': hdrs, 'client': (client, 5000)}
        asyncio.run(self.mw(scope, receive, send))
        return sent[0]['status'], dict(sent[0]['headers'])

    def test_without_password_asks_browser_for_it(self):
        status, headers = self.call()
        self.assertEqual(status, 401)
        self.assertTrue(headers[b'www-authenticate'].startswith(b'Basic'))

    def test_right_password_passes_and_any_user_is_accepted(self):
        self.assertEqual(self.call(headers={'authorization': basic('segredo')})[0], 200)
        self.assertEqual(self.call(headers={'authorization': basic('segredo', '')})[0], 200)

    def test_wrong_or_malformed_password_is_rejected(self):
        self.assertEqual(self.call(headers={'authorization': basic('errada')})[0], 401)
        self.assertEqual(self.call(headers={'authorization': 'Basic %%%'})[0], 401)
        self.assertEqual(self.call(headers={'authorization': 'Bearer segredo'})[0], 401)

    def test_password_does_not_skip_csrf_checks(self):
        auth = {'authorization': basic('segredo')}
        self.assertEqual(self.call('POST', auth)[0], 403)
        self.assertEqual(self.call('POST', {**auth, 'x-autocortes': '1'})[0], 200)

    def test_lockout_after_repeated_failures_is_per_client(self):
        with mock.patch.object(config, 'AUTH_MAX_FAILURES', 3):
            for _ in range(3):
                self.assertEqual(self.call(headers={'authorization': basic('x')})[0], 401)
            self.assertEqual(self.call(headers={'authorization': basic('segredo')})[0], 429)
            self.assertEqual(self.call(headers={'authorization': basic('segredo')}, client='192.168.0.30')[0], 200)

    def test_wildcard_host_still_requires_origin_to_match_host(self):
        self.mw.allowed_hosts = {'*'}
        ok = {'authorization': basic('segredo'), 'host': 'cortes.example.com', 'x-autocortes': '1'}
        self.assertEqual(self.call('GET', ok)[0], 200)
        self.assertEqual(self.call('POST', {**ok, 'origin': 'https://cortes.example.com'})[0], 200)
        self.assertEqual(self.call('POST', {**ok, 'origin': 'https://evil.example'})[0], 403)

    def test_suffix_entry_allows_tunnel_subdomains_only(self):
        self.mw.allowed_hosts = {'127.0.0.1', '.trycloudflare.com'}
        ok = {'authorization': basic('segredo'), 'host': 'abc-def.trycloudflare.com', 'x-autocortes': '1'}
        self.assertEqual(self.call('GET', ok)[0], 200)
        self.assertEqual(self.call('POST', {**ok, 'origin': 'https://abc-def.trycloudflare.com'})[0], 200)
        self.assertEqual(self.call('POST', {**ok, 'origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.call('GET', {**ok, 'host': 'trycloudflare.com'})[0], 400)
        self.assertEqual(self.call('GET', {**ok, 'host': 'eviltrycloudflare.com'})[0], 400)

    def test_lockout_behind_tunnel_uses_real_visitor_ip(self):
        with mock.patch.object(config, 'AUTH_MAX_FAILURES', 3):
            for _ in range(3):
                self.call(headers={'authorization': basic('x'), 'cf-connecting-ip': '203.0.113.5'}, client='127.0.0.1')
            self.assertEqual(self.call(headers={'authorization': basic('segredo'), 'cf-connecting-ip': '203.0.113.5'},
                                       client='127.0.0.1')[0], 429)
            # o dono (outro IP, ou direto no PC) continua entrando
            self.assertEqual(self.call(headers={'authorization': basic('segredo'), 'cf-connecting-ip': '198.51.100.7'},
                                       client='127.0.0.1')[0], 200)
            self.assertEqual(self.call(headers={'authorization': basic('segredo')}, client='127.0.0.1')[0], 200)

    def test_forwarded_ip_header_is_ignored_from_other_devices(self):
        with mock.patch.object(config, 'AUTH_MAX_FAILURES', 3):
            for i in range(3):  # trocar o cabeçalho não escapa do bloqueio
                self.call(headers={'authorization': basic('x'), 'cf-connecting-ip': f'10.0.0.{i}'})
            self.assertEqual(self.call(headers={'authorization': basic('segredo')})[0], 429)


class LauncherTests(unittest.TestCase):
    def run_main(self, **env):
        import os
        full = {**os.environ, **env}
        return subprocess.run([sys.executable, '-c', 'import sys; from app.__main__ import main; sys.exit(main())'],
                              cwd=config.ROOT, env=full, capture_output=True, text=True, timeout=60)

    def test_refuses_to_expose_without_password(self):
        result = self.run_main(AUTOCORTES_HOST='0.0.0.0', AUTOCORTES_PASSWORD='')
        self.assertEqual(result.returncode, 2)
        self.assertIn('AUTOCORTES_PASSWORD', result.stderr)

    def test_exposed_mode_allows_the_machine_ips_as_host(self):
        code = 'from app import config; print(config.is_exposed(), sorted(config.ALLOWED_HOSTS))'
        import os
        out = subprocess.run([sys.executable, '-c', code], cwd=config.ROOT, capture_output=True, text=True,
                             env={**os.environ, 'AUTOCORTES_HOST': '0.0.0.0'}, timeout=60).stdout
        self.assertTrue(out.startswith('True'))
        local = subprocess.run([sys.executable, '-c', code], cwd=config.ROOT, capture_output=True, text=True,
                               env={k: v for k, v in os.environ.items() if k != 'AUTOCORTES_HOST'}, timeout=60).stdout
        self.assertTrue(local.startswith('False'))


if __name__ == '__main__':
    unittest.main()
