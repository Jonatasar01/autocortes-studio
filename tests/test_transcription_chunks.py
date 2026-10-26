"""Áudio longo é transcrito em partes: memória fixa, cortes no silêncio e tempos contínuos."""
from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import config, transcription

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


def write_wav(path: Path, samples) -> Path:
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.astype('<i2').tobytes())
    return path


@unittest.skipIf(np is None, 'numpy ausente')
class ChunkTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='chunks-'))
        rng = np.random.default_rng(1)
        # 10 s de "fala" (ruído) com silêncios em 2,6 s e 6,1 s
        audio = (rng.standard_normal(16000 * 10) * 8000).clip(-32000, 32000)
        for gap in (2.6, 6.1):
            audio[int(gap * 16000):int((gap + 0.3) * 16000)] = 0
        self.audio = audio.astype('<i2')
        self.wav = write_wav(self.dir / 'a.wav', self.audio)

    def test_chunks_cut_in_silence_and_keep_every_sample(self):
        parts = list(transcription._wav_chunks(self.wav, chunk_seconds=4))
        offsets = [o for o, _ in parts]
        self.assertEqual(offsets[0], 0.0)
        self.assertEqual(sum(len(a) for _, a in parts), len(self.audio))
        for o in offsets[1:]:
            self.assertTrue(2.6 <= o <= 2.9 or 6.1 <= o <= 6.4, offsets)   # dentro de um silêncio
        joined = np.concatenate([a for _, a in parts])
        self.assertTrue(np.allclose(joined * 32768.0, self.audio))
        self.assertEqual(parts[0][1].dtype, np.float32)

    def test_long_audio_is_transcribed_in_parts_with_offsets(self):
        calls = []

        class Model:
            def transcribe(self, audio, language=None, **_kw):
                calls.append((type(audio).__name__, language))
                seg = SimpleNamespace(start=0.5, end=1.0, text='parte', no_speech_prob=0.0, avg_logprob=-0.2,
                                      words=[SimpleNamespace(start=0.5, end=0.8, word=' parte')])
                return iter([seg]), SimpleNamespace(language='pt')

        progress = []
        with mock.patch.object(transcription, 'available', return_value=True), \
                mock.patch.object(transcription, '_load_model', return_value=Model()), \
                mock.patch.object(transcription, '_gpu_failed', True), \
                mock.patch.object(transcription, 'CHUNK_SECONDS', 4), \
                mock.patch.object(config, 'WHISPER_LANGUAGE', 'auto'):
            result, status = transcription.transcribe(self.wav, 10.0, on_progress=lambda f, _m: progress.append(f))
        self.assertEqual(status['state'], 'ok')
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(kind == 'ndarray' for kind, _ in calls))
        self.assertEqual([lang for _, lang in calls], [None, 'pt', 'pt'])   # idioma fixado após a 1ª parte
        starts = [seg['start'] for seg in result]
        self.assertEqual(starts[0], 0.5)
        self.assertTrue(all(b > a + 2 for a, b in zip(starts, starts[1:])), starts)
        self.assertEqual(result[1]['words'][0]['start'], result[1]['start'])
        self.assertEqual(progress, sorted(progress))

    def test_short_audio_keeps_single_pass_on_the_file(self):
        seen = []

        class Model:
            def transcribe(self, audio, **_kw):
                seen.append(audio)
                return iter([]), SimpleNamespace(language='pt')

        with mock.patch.object(transcription, 'available', return_value=True), \
                mock.patch.object(transcription, '_load_model', return_value=Model()), \
                mock.patch.object(transcription, '_gpu_failed', True):
            _, status = transcription.transcribe(self.wav, 10.0)
        self.assertEqual(seen, [str(self.wav)])
        self.assertEqual(status['state'], 'no_speech')


def w(start, end, word):
    return {'start': start, 'end': end, 'word': word}


class SentenceSplitTests(unittest.TestCase):
    def test_splits_on_punctuation_pause_and_length(self):
        words = [w(0.0, 0.4, 'Olá'), w(0.5, 1.2, 'pessoal.'), w(1.3, 1.6, 'Hoje'), w(1.7, 2.0, 'tem'),
                 w(3.2, 3.5, 'live'), w(3.6, 4.0, 'longa')]          # pausa de 1,2 s antes de "live"
        words += [w(4.1 + i * 0.5, 4.5 + i * 0.5, 'palavra') for i in range(30)]   # fala corrida sem ponto
        parts = transcription.split_sentences(words)
        self.assertEqual(parts[0][2], 'Olá pessoal.')
        self.assertEqual(parts[1][2], 'Hoje tem')
        self.assertTrue(parts[2][2].startswith('live longa'))
        self.assertTrue(all(p[1] - p[0] <= transcription.SENTENCE_MAX_SECONDS + 0.5 for p in parts))
        self.assertEqual(sum(len(p[3]) for p in parts), len(words))

    def test_gpu_uses_batched_pipeline_and_splits_sentences(self):
        calls = {}

        class Pipeline:
            def transcribe(self, audio, **kw):
                calls.update(kw)
                seg = SimpleNamespace(start=0.0, end=4.0, text=' Olá pessoal. Tudo bem?', no_speech_prob=0.0,
                                      avg_logprob=-0.1, words=[
                                          SimpleNamespace(start=0.0, end=0.5, word=' Olá'),
                                          SimpleNamespace(start=0.6, end=1.2, word=' pessoal.'),
                                          SimpleNamespace(start=2.0, end=2.5, word=' Tudo'),
                                          SimpleNamespace(start=2.6, end=3.4, word=' bem?')])
                return iter([seg]), SimpleNamespace(language='pt')

        with mock.patch.object(transcription, 'available', return_value=True), \
                mock.patch.object(transcription, '_load_model', return_value=object()), \
                mock.patch.object(transcription, '_batched_pipeline', return_value=Pipeline()), \
                mock.patch.object(transcription, '_gpu_failed', False), \
                mock.patch.object(config, 'WHISPER_DEVICE', 'cuda'):
            result, status = transcription.transcribe(Path('a.wav'), 4.0)
        self.assertEqual(status['device'], 'cuda')
        self.assertEqual(calls['batch_size'], transcription.GPU_BATCH_SIZE)
        self.assertNotIn('condition_on_previous_text', calls)
        self.assertEqual([r['text'] for r in result], ['Olá pessoal.', 'Tudo bem?'])
        self.assertEqual((result[1]['start'], result[1]['end']), (2.0, 3.4))


if __name__ == '__main__':
    unittest.main()
