"""Título, descrição e hashtags de publicação escritos pela IA a partir da fala do corte (IA simulada)."""
from __future__ import annotations

import unittest
from unittest import mock

from app import copywriting, curation, settings, storage
from app.errors import AppError

PROJECT = {'channel': 'Coringa', 'transcript': [
    {'start': 9.0, 'end': 12.0, 'text': 'fora do corte'},
    {'start': 10.0, 'end': 14.0, 'text': 'Mano, eu não acredito que o carro'},
    {'start': 14.0, 'end': 18.0, 'text': 'pegou fogo bem na frente da live, olha isso galera'}],
    'clips': [{'id': 1, 'start': 10.0, 'end': 18.0, 'title': 'Mano eu não acredito',
               'description': 'Mano, eu não acredito', 'community': {'title': 'CARRO PEGOU FOGO'}}]}


class CopywritingTests(unittest.TestCase):
    def run_suggest(self, provider='ollama', configured=True, reply=None, error=None, consent=False):
        calls = []

        def ask(prov, system, user, schema, name, max_tokens=0):
            calls.append((prov, user))
            if error:
                raise error
            return reply

        cfg = {**settings.load()['curation'], 'provider': provider, 'consent': consent}
        with mock.patch.object(storage, 'read_project', lambda pid: PROJECT), \
                mock.patch.object(storage, 'edit_project', mock.MagicMock()), \
                mock.patch.object(settings, 'load', lambda: {'curation': cfg}), \
                mock.patch.object(curation, 'is_configured', lambda p: configured), \
                mock.patch.object(curation, 'ask', ask):
            return copywriting.suggest('p', 1, '9:16'), calls

    def test_ai_writes_from_what_was_said(self):
        out, calls = self.run_suggest(reply={'title': '  O carro pegou fogo AO VIVO 🔥 ',
                                             'description': 'Ninguém esperava isso no meio da live.',
                                             'hashtags': ['#Carro', 'incêndio', '#shorts', 'live coringa!']})
        self.assertEqual(out['title'], 'O carro pegou fogo AO VIVO 🔥')
        self.assertEqual(out['hashtags'], ['#carro', '#incendio', '#livecoringa', '#shorts'])
        self.assertEqual(out['provider'], curation.PROVIDER_LABELS['ollama'])
        prompt = calls[0][1]
        self.assertIn('pegou fogo bem na frente', prompt)
        self.assertIn('CARRO PEGOU FOGO', prompt)      # título do clipe do público ajuda
        self.assertIn('Coringa', prompt)

    def test_gemini_only_with_consent(self):
        _, calls = self.run_suggest('gemini', reply={'title': 'x', 'description': '', 'hashtags': []})
        self.assertEqual(calls[0][0], 'ollama')
        _, calls = self.run_suggest('gemini', consent=True, reply={'title': 'x', 'description': '', 'hashtags': []})
        self.assertEqual(calls[0][0], 'gemini')

    def test_without_ai_or_on_failure_falls_back(self):
        out, calls = self.run_suggest(configured=False)
        self.assertEqual((out['title'], out['provider'], calls), ('Mano eu não acredito', None, []))
        out, _ = self.run_suggest(error=AppError('Ollama desligado.', 502, 'x'))
        self.assertIn('Ollama desligado', out['warning'])
        self.assertIn('#shorts', out['hashtags'])
        out, _ = self.run_suggest(reply={'title': '', 'description': 'd', 'hashtags': []})
        self.assertIsNone(out['provider'])


if __name__ == '__main__':
    unittest.main()
