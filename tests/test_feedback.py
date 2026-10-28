"""Feedback nos cortes (bom/rejeitado): fica no corte e num registro do projeto que sobrevive a refazer a seleção."""
from __future__ import annotations

import unittest

from app import service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class FeedbackTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'feedback.mp4', size='640x360', duration=40, fps=25)

    def test_mark_unmark_and_survive_reselect(self):
        res = self.create(self.video)
        clips = storage.read_project(res['id'])['clips']
        good, bad = clips[0], clips[1]
        out = service.set_feedback(res['id'], good['id'], 'bom')
        self.assertEqual(out['feedback']['verdict'], 'bom')
        service.set_feedback(res['id'], bad['id'], 'rejeitado', 'começa no meio da frase')
        p = storage.read_project(res['id'])
        self.assertEqual({e['verdict'] for e in p['feedback_log']}, {'bom', 'rejeitado'})
        self.assertEqual(next(c for c in p['clips'] if c['id'] == bad['id'])['feedback']['reason'],
                         'começa no meio da frase')
        # Trocar a marcação do mesmo trecho não duplica o registro.
        counts = service.set_feedback(res['id'], bad['id'], 'bom')['counts']
        self.assertEqual(counts, {'bom': 2, 'rejeitado': 0})
        # Desmarcar.
        self.assertIsNone(service.set_feedback(res['id'], bad['id'], None)['feedback'])
        self.assertEqual(len(storage.read_project(res['id'])['feedback_log']), 1)
        # Refazer a seleção troca os cortes, mas o registro continua (é a referência de avaliação).
        job = service.reselect(res['id'])
        self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        log = storage.read_project(res['id'])['feedback_log']
        self.assertEqual([(e['verdict'], e['start'], e['end']) for e in log],
                         [('bom', good['start'], good['end'])])

    def test_invalid_verdict(self):
        res = self.create(self.video)
        clip = storage.read_project(res['id'])['clips'][0]
        with self.assertRaises(AppError):
            service.set_feedback(res['id'], clip['id'], 'otimo')


if __name__ == '__main__':
    unittest.main()
