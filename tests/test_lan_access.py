"""Senha para abrir pela rede: neste computador nunca pede; de outro aparelho, só com a senha certa."""
from __future__ import annotations

import base64
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, lan_access, secrets_store
from app.errors import AppError
from app.main import app


def basic(password: str, user: str = 'qualquer') -> dict:
    return {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()}


class LanAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp
        self.client = TestClient(app)          # o TestClient chega como "testclient": um aparelho da rede

    def tearDown(self):
        lan_access.set_password('')
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_without_password_the_network_stays_as_before(self):
        self.assertEqual(self.client.get('/api/health').status_code, 200)

    def test_network_needs_the_password_and_the_computer_does_not(self):
        lan_access.set_password('senha-da-live')
        self.assertNotIn('senha-da-live', secrets_store.get('lan_password'))   # só o hash fica guardado
        r = self.client.get('/')
        self.assertEqual(r.status_code, 401)
        self.assertIn('Basic', r.headers['www-authenticate'])   # o navegador pede usuário e senha sozinho
        self.assertEqual(r.json()['code'], 'auth_required')
        self.assertEqual(self.client.get('/api/health', headers=basic('errada')).status_code, 401)
        self.assertEqual(self.client.get('/api/health', headers=basic('senha-da-live')).status_code, 200)
        self.assertEqual(self.client.get('/api/health', headers=basic('senha-da-live', 'outro')).status_code, 200)
        self.assertTrue(lan_access.allowed('127.0.0.1', None))
        self.assertTrue(lan_access.allowed('::1', None))
        self.assertFalse(lan_access.allowed('192.168.0.50', None))

    def test_password_changes_only_on_this_computer(self):
        r = self.client.put('/api/settings/lan-password', json={'password': 'nova-senha'},
                            headers={'X-AutoCortes': '1'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(lan_access.enabled())
        with self.assertRaises(AppError):
            lan_access.set_password('123')
        lan_access.set_password('primeira')
        lan_access.set_password('segunda')
        self.assertFalse(lan_access.check('primeira'))
        self.assertTrue(lan_access.check('segunda'))
        self.assertTrue(lan_access.allowed('10.0.0.2', basic('segunda')['Authorization']))
        lan_access.set_password('')
        self.assertTrue(lan_access.allowed('10.0.0.2', None))


if __name__ == '__main__':
    unittest.main()
