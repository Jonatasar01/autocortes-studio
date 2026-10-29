"""Chave do Gemini colada na tela: guardada em data/secrets.json e nunca devolvida pela API."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, curation, secrets_store
from app.errors import AppError, sanitize

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    HAS_FASTAPI = False

KEY = 'AIzaSyTESTE-chave-de-mentira_1234abcd'
H = {'X-AutoCortes': '1'}


class SecretsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='autocortes-secrets-'))
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp / 'data'
        self.env = mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GOOGLE_API_KEY': ''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_saves_reads_and_removes(self):
        self.assertEqual(secrets_store.get('gemini'), '')
        secrets_store.put('gemini', '  ' + KEY + '  ')
        self.assertEqual(secrets_store.get('gemini'), KEY)
        self.assertEqual(secrets_store.hint('gemini'), 'abcd')
        self.assertTrue((config.DATA_DIR / 'secrets.json').is_file())
        self.assertFalse(list(config.DATA_DIR.glob('.secrets-*.tmp')))
        secrets_store.put('gemini', '')
        self.assertEqual(secrets_store.get('gemini'), '')
        self.assertIsNone(secrets_store.hint('gemini'))

    def test_rejects_invalid_keys_and_unknown_names(self):
        for bad in ('curta', 'com espaço no meio de uma chave longa', 'x' * 201, 'AIza\nquebra-de-linha-123456789'):
            with self.assertRaises(AppError):
                secrets_store.put('gemini', bad)
        with self.assertRaises(AppError):
            secrets_store.put('openai', KEY)
        self.assertEqual(secrets_store.get('gemini'), '')

    def test_corrupted_file_counts_as_no_key(self):
        config.DATA_DIR.mkdir(parents=True)
        (config.DATA_DIR / 'secrets.json').write_text('{quebrado', encoding='utf-8')
        self.assertEqual(secrets_store.get('gemini'), '')

    def test_saved_key_wins_over_environment(self):
        os.environ['GEMINI_API_KEY'] = 'AIzaVARIAVEL-de-ambiente-0000000000'
        self.assertEqual(curation.status()['gemini']['key_source'], 'env')
        secrets_store.put('gemini', KEY)
        self.assertEqual(curation.key_for('gemini'), KEY)
        status = curation.status()['gemini']
        self.assertTrue(status['configured'])
        self.assertEqual((status['key_source'], status['key_hint']), ('saved', 'abcd'))

    def test_sanitize_hides_the_saved_key(self):
        secrets_store.put('gemini', 'chave-sem-formato-google-0123456789')
        self.assertNotIn('chave-sem-formato-google', sanitize('falhou com chave-sem-formato-google-0123456789'))

    @unittest.skipUnless(HAS_FASTAPI, 'FastAPI/httpx ausente')
    def test_api_never_returns_the_key(self):
        from app.main import app
        client = TestClient(app)
        self.assertEqual(client.put('/api/settings/keys/gemini', json={'key': KEY}).status_code, 403)
        r = client.put('/api/settings/keys/gemini', json={'key': KEY}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()['key_source'], r.json()['key_hint']), ('saved', 'abcd'))
        for body in (r.text, client.get('/api/settings').text):
            self.assertNotIn(KEY, body)
        self.assertEqual(client.put('/api/settings/keys/openai', json={'key': KEY}, headers=H).status_code, 422)
        self.assertEqual(client.put('/api/settings/keys/gemini', json={'key': 'curta'}, headers=H).status_code, 400)
        r = client.delete('/api/settings/keys/gemini', headers=H)
        self.assertEqual((r.status_code, r.json()['configured']), (200, False))
        self.assertEqual(secrets_store.get('gemini'), '')


if __name__ == '__main__':
    unittest.main()
