"""Narração por voz neural: validação da entrada e gravação atômica (sem rede: o edge-tts é simulado)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.audio import tts_generator
from app.errors import AppError


class FakeCommunicate:
    calls: list[tuple] = []
    fail: Exception | None = None

    def __init__(self, text, voice, *, rate='+0%'):
        FakeCommunicate.calls.append((text, voice, rate))

    def save_sync(self, path):
        if FakeCommunicate.fail:
            Path(path).write_bytes(b'parcial')
            raise FakeCommunicate.fail
        Path(path).write_bytes(b'ID3fake-mp3')


class TtsTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='tts-'))
        FakeCommunicate.calls, FakeCommunicate.fail = [], None
        fake = mock.MagicMock(Communicate=FakeCommunicate)
        patcher = mock.patch.dict('sys.modules', {'edge_tts': fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_synthesize_writes_mp3_with_voice(self):
        out = tts_generator.synthesize('  Você   sabia?\n\n Pois é. ', self.dir / 'sub' / 'voz.mp3',
                                       voice='pt-BR-FranciscaNeural', rate='+10%')
        self.assertEqual(out.read_bytes(), b'ID3fake-mp3')
        self.assertEqual(FakeCommunicate.calls, [('Você sabia?\nPois é.', 'pt-BR-FranciscaNeural', '+10%')])
        self.assertEqual(list(out.parent.iterdir()), [out])  # nenhum temporário sobrando

    def test_invalid_input_is_rejected_before_network(self):
        for kwargs, code in (({'text': '  '}, 'tts_empty'), ({'text': 'x' * 6000}, 'tts_too_long'),
                             ({'text': 'oi', 'voice': 'Antonio'}, 'tts_voice'),
                             ({'text': 'oi', 'rate': 'rápido'}, 'tts_rate')):
            with self.subTest(code=code), self.assertRaises(AppError) as ctx:
                tts_generator.synthesize(dest=self.dir / 'x.mp3', **kwargs)
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(FakeCommunicate.calls, [])

    def test_service_failure_leaves_no_partial_file(self):
        FakeCommunicate.fail = OSError('sem conexão')
        with self.assertRaises(AppError) as ctx:
            tts_generator.synthesize('Olá.', self.dir / 'voz.mp3')
        self.assertIn('sem conexão', ctx.exception.message)
        self.assertEqual(list(self.dir.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
