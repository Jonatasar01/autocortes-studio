"""Área do "Só o streamer" desenhada à mão: guardada no corte, usada na exportação no lugar da detecção."""
from __future__ import annotations

import unittest
from unittest import mock

from app import media, pipeline, service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase

INFO = {'width': 1920, 'height': 1080, 'video_index': 0, 'duration': 6.0}


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class FocusRectTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'foco.mp4', size='640x360', duration=12, fps=25)

    def test_set_validate_and_clear(self):
        res = self.create(self.video)
        cid = storage.read_project(res['id'])['clips'][0]['id']
        out = service.set_focus_rect(res['id'], cid, [0.7, 0.02, 0.5, 0.3])      # largura passa da borda: ajusta
        self.assertEqual(out['focus_rect'], [0.7, 0.02, 0.3, 0.3])
        for bad in ([0.1, 0.1, 0.01, 0.3], [0.1, 0.1], [0.1, 0.1, float('nan'), 0.2], 'x'):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                service.set_focus_rect(res['id'], cid, bad)
        self.assertIsNone(service.set_focus_rect(res['id'], cid, None)['focus_rect'])
        self.assertNotIn('focus_rect', storage.read_project(res['id'])['clips'][0])

    def test_export_uses_the_drawn_area_without_face_analysis(self):
        res = self.create(self.video)
        pid = res['id']
        cid = storage.read_project(pid)['clips'][0]['id']
        service.set_focus_rect(pid, cid, [0.6, 0.0, 0.4, 0.4])
        with mock.patch.object(pipeline, '_cached_track', side_effect=AssertionError('não devia analisar rostos')):
            job = service.request_export(pid, cid, {'format': '9:16', 'framing': 'streamer', 'subtitles': False})
            done = service.manager.wait(job['id'], 180)
        self.assertEqual(done['status'], 'done', done.get('error'))
        self.assertEqual(done['params']['focus_rect'], [0.6, 0.0, 0.4, 0.4])
        record = storage.read_project(pid)['clips'][0]['exports']['9:16']
        self.assertTrue(any('área desenhada à mão' in w for w in record['warnings']))
        # Sem "Só o streamer", a área não entra na exportação.
        plain = service.request_export(pid, cid, {'format': '16:9', 'framing': 'blur', 'subtitles': False})
        self.assertNotIn('focus_rect', service.manager.get(plain['id'])['params'])
        service.manager.wait(plain['id'], 180)


class FocusGraphTests(unittest.TestCase):
    def test_streamer_segment_uses_the_area_also_in_horizontal(self):
        track = {'ok': False, 'shots': [], 'facecam': [0.6, 0.0, 0.4, 0.4]}
        segs = [{'start': 0, 'end': 3, 'framing': 'streamer'}, {'start': 3, 'end': 6, 'framing': 'blur'}]
        for fmt in ('9:16', '16:9'):
            with self.subTest(fmt=fmt):
                graph, _ = media.build_filtergraph(INFO, fmt, 'mix', None, None, track, '', segs)
                self.assertIn('crop=w=iw*0.4000:h=ih*0.4000:x=iw*0.6000:y=ih*0.0000', graph)


if __name__ == '__main__':
    unittest.main()
