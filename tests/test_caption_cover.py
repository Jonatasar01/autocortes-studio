"""Legenda já gravada no vídeo: a área marcada (uma por projeto) é apagada em todas as exportações."""
from __future__ import annotations

import re
import subprocess
import unittest

from app import media, service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase

INFO = {'width': 640, 'height': 360, 'video_index': 0, 'duration': 6.0}
COVER = [0.1, 0.66, 0.8, 0.16]


def region_luma(path, fmt_filter: str = '') -> float:
    """Brilho médio da faixa da legenda (a mesma área de ``COVER``) no quadro do meio."""
    out = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '1.5', '-i', str(path), '-frames:v', '1', '-vf',
                          f'{fmt_filter}crop=iw*0.7:ih*0.08:iw*0.15:ih*0.7,signalstats,'
                          'metadata=print:key=lavfi.signalstats.YAVG:file=-', '-f', 'null', '-'],
                         capture_output=True, text=True, check=True).stdout
    return float(re.findall(r'YAVG=([0-9.]+)', out)[-1])


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class CaptionCoverTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Fundo cinza com uma "legenda" branca gravada na imagem, na faixa de baixo.
        cls.video = make_video(cls.tmp / 'legendado.mp4', size='640x360', duration=6, fps=25,
                               extra=['-vf', 'drawbox=x=0:y=0:w=iw:h=ih:color=gray@1:t=fill,'
                                             'drawbox=x=96:y=252:w=448:h=28:color=white@1:t=fill'])

    def test_set_validate_and_clear(self):
        pid = self.create(self.video)['id']
        self.assertEqual(service.set_caption_cover(pid, [0.1, 0.9, 0.8, 0.3])['caption_cover'], [0.1, 0.9, 0.8, 0.1])
        for bad in ([0.1, 0.5, 0.8, 0.01], [0.1, 0.1], [0.1, 0.1, float('inf'), 0.2], 'x'):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                service.set_caption_cover(pid, bad)
        self.assertIsNone(service.set_caption_cover(pid, None)['caption_cover'])
        self.assertNotIn('caption_cover', storage.read_project(pid))

    def test_export_erases_the_marked_area(self):
        pid = self.create(self.video)['id']
        cid = storage.read_project(pid)['clips'][0]['id']
        self.assertGreater(region_luma(self.video), 200)            # a legenda antiga está lá
        service.set_caption_cover(pid, COVER)
        job = service.request_export(pid, cid, {'format': 'original', 'framing': 'fit', 'subtitles': False})
        done = service.manager.wait(job['id'], 180)
        self.assertEqual(done['status'], 'done', done.get('error'))
        self.assertEqual(done['params']['caption_cover'], COVER)
        out = storage.project_dir(pid) / 'clips' / storage.read_project(pid)['clips'][0]['exports']['original']['file']
        self.assertLess(abs(region_luma(out) - 128), 20)            # virou o cinza do fundo


class CaptionCoverGraphTests(unittest.TestCase):
    def test_erased_before_any_crop_in_every_format(self):
        boxed = {**INFO, 'caption_cover': COVER, 'letterbox': {'w': 640, 'h': 300, 'x': 0, 'y': 30}}
        for fmt, framing in (('9:16', 'blur'), ('16:9', 'fit'), ('original', 'fit')):
            with self.subTest(fmt=fmt):
                graph, _ = media.build_filtergraph(boxed, fmt, framing, None)
                self.assertTrue(graph.startswith('[0:0]delogo=x=64:y=238:w=512:h=58,'), graph)

    def test_area_touching_the_edges_stays_inside_the_frame(self):
        self.assertEqual(media.caption_cover_filter({**INFO, 'caption_cover': [0.0, 0.8, 1.0, 0.2]}),
                         'delogo=x=1:y=288:w=638:h=71,')
        self.assertEqual(media.caption_cover_filter(INFO), '')


if __name__ == '__main__':
    unittest.main()
