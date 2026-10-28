"""Perfis de exportação: prontos por tipo de conteúdo e salvos pelo usuário nas configurações."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app import config, media, settings
from app.errors import AppError

PROFILE = {'name': 'Shorts do canal', 'format': '9:16', 'framing': 'split', 'subtitles': True,
           'subtitle_style': 'dinamica', 'refine_subtitles': False, 'normalize_audio': True, 'hook': True,
           'filter': 'vivido'}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp / 'data'
        config.DATA_DIR.mkdir()

    def tearDown(self):
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builtin_profiles_are_valid_export_options(self):
        public = settings.public()
        self.assertEqual(public['export_profiles'], [])
        for p in public['builtin_profiles']:
            self.assertIn(p['format'], media.FORMATS)
            self.assertIn(p['framing'], media.FRAMINGS)
            self.assertEqual(set(settings.PROFILE_FIELDS) - set(p), set())

    def test_save_replace_and_delete(self):
        s = settings.update({'export_profiles': [PROFILE]})
        self.assertEqual(s['export_profiles'], [PROFILE])
        self.assertEqual(settings.load()['export_profiles'], [PROFILE])   # gravado em disco
        # Espaços extras no nome e filtro "nenhum" são normalizados; campos desconhecidos somem.
        s = settings.update({'export_profiles': [{**PROFILE, 'name': '  Shorts   do canal ', 'filter': 'nenhum',
                                                  'extra': 1}]})
        self.assertEqual(s['export_profiles'][0]['name'], 'Shorts do canal')
        self.assertIsNone(s['export_profiles'][0]['filter'])
        self.assertNotIn('extra', s['export_profiles'][0])
        self.assertEqual(settings.update({'export_profiles': []})['export_profiles'], [])

    def test_invalid_profiles(self):
        for bad in ([{**PROFILE, 'format': '4:3'}], [{**PROFILE, 'framing': 'zoom'}], [{**PROFILE, 'name': ' '}],
                    [PROFILE, {**PROFILE, 'name': 'shorts DO canal'}],          # nome repetido
                    [{**PROFILE, 'name': 'Live de jogo'}],                        # nome de um perfil pronto
                    [{**PROFILE, 'subtitles': 'sim'}], 'perfil',
                    [{**PROFILE, 'name': f'p{i}'} for i in range(settings.MAX_PROFILES + 1)]):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(AppError):
                settings.update({'export_profiles': bad})
        self.assertEqual(settings.load()['export_profiles'], [])


if __name__ == '__main__':
    unittest.main()
