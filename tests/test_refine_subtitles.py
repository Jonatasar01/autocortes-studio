"""Legenda precisa: o trecho do corte é retranscrito com o modelo maior e substitui a transcrição no projeto."""
from __future__ import annotations

import unittest
from unittest import mock

from app import pipeline, service, storage, transcription
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


class FakeCtx:
    def __init__(self, pid):
        self.project_id, self.cancel_event, self.stages = pid, None, []

    def stage(self, name, message):
        self.stages.append(name)


def seg(start, end, text):
    return {'start': start, 'end': end, 'text': text, 'avg_logprob': -0.2, 'no_speech_prob': 0.0,
            'words': [{'start': start, 'end': end, 'word': text}]}


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class RefineTranscriptTests(ServiceBase):
    def setUp(self):
        video = make_video(self.tmp / 'refino.mp4', size='640x360', duration=20, fps=25)
        self.pid = self.create(video)['id']
        with storage.edit_project(self.pid) as p:
            p['transcript'] = [seg(1, 3, 'antes'), seg(5, 7, 'curinguinha a júnior'), seg(9, 11, 'meio'),
                               seg(15, 17, 'depois')]
        self.folder = storage.project_dir(self.pid)

    def run_refine(self, start, end, available=True, segments=None):
        p = storage.read_project(self.pid)
        warnings: list[str] = []
        ctx = FakeCtx(self.pid)
        # O áudio extraído começa 1 s antes do corte: tempos do modelo são relativos a esse início.
        fake = mock.Mock(return_value=(segments if segments is not None else
                                       [seg(1.0, 3.0, 'Coringuinha Júnior'), seg(5.0, 7.0, 'no meio certo')],
                                       {'state': 'ok', 'model': 'large-v3-turbo'}))
        with mock.patch.object(transcription, 'refine_available', return_value=available), \
                mock.patch.object(transcription, 'refine', fake):
            out = pipeline._refine_transcript(ctx, self.folder, p, p['media'], start, end, warnings)
        return out, warnings, ctx, fake

    def test_replaces_only_the_clip_range_and_marks_it(self):
        out, warnings, ctx, fake = self.run_refine(4.0, 12.0)
        self.assertEqual(warnings, [])
        self.assertIn('legenda', ctx.stages)
        texts = [s['text'] for s in storage.read_project(self.pid)['transcript']]
        self.assertEqual(texts, ['antes', 'Coringuinha Júnior', 'no meio certo', 'depois'])
        refined = storage.read_project(self.pid)['transcript']
        self.assertEqual((refined[1]['start'], refined[1]['words'][0]['start']), (4.0, 4.0))   # 3 + 1 s de folga
        self.assertEqual(out['transcript_refined'][-1][:2], [4.0, 12.0])
        # Segunda vez no mesmo corte: não retranscreve.
        _, _, _, again = self.run_refine(4.0, 12.0)
        again.assert_not_called()

    def test_without_gpu_keeps_the_analysis_transcript(self):
        out, warnings, ctx, fake = self.run_refine(4.0, 12.0, available=False)
        fake.assert_not_called()
        self.assertTrue(any('GPU' in w for w in warnings))
        self.assertIn('curinguinha a júnior', [s['text'] for s in out['transcript']])

    def test_export_option_is_recorded(self):
        clip = service.get_project(self.pid)['clips'][0]
        with mock.patch.object(service._jobs(), 'submit', return_value=({'id': 'x', 'params': {}}, True)) as sub:
            with mock.patch.object(service, 'public_job', side_effect=lambda j: j):
                service.request_export(self.pid, clip['id'], {'format': '9:16', 'refine_subtitles': False})
        self.assertFalse(sub.call_args[0][2]['refine_subtitles'])


if __name__ == '__main__':
    unittest.main()
