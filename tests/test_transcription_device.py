"""Depois de uma falha da GPU, a transcrição não tenta o CUDA de novo (podia travar o processo)."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import config, transcription


class FakeModel:
    def __init__(self, device: str):
        self.device = device

    def transcribe(self, *_args, **_kwargs):
        if self.device != 'cpu':
            raise RuntimeError('Library cublas64_12.dll is not found or cannot be loaded')
        seg = SimpleNamespace(start=0.0, end=1.0, text='olá', words=[], no_speech_prob=0.0, avg_logprob=-0.2)
        return iter([seg]), SimpleNamespace(language='pt')


class GpuFallbackTests(unittest.TestCase):
    def test_gpu_failure_sticks_to_cpu(self):
        loaded: list[str] = []

        def load(device):
            loaded.append(device)
            return FakeModel(device)

        with mock.patch.object(transcription, 'available', return_value=True), \
                mock.patch.object(transcription, '_load_model', side_effect=load), \
                mock.patch.object(transcription, '_gpu_failed', False), \
                mock.patch.object(config, 'WHISPER_DEVICE', 'auto'):
            first, status = transcription.transcribe(Path('a.wav'), 1.0)
            self.assertEqual(status['device'], 'cpu')
            second, status = transcription.transcribe(Path('b.wav'), 1.0)
            self.assertEqual(status['device'], 'cpu')
        self.assertEqual(loaded, ['auto', 'cpu', 'cpu'])
        self.assertEqual(first[0]['text'], 'olá')


if __name__ == '__main__':
    unittest.main()
