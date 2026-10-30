"""Seguir rosto: legenda fora do rosto e a variação com o rosto numa janela sobre fundo desfocado."""
from __future__ import annotations

import subprocess
import unittest

from app import media, reframe, subtitles
from tests.helpers import HAS_FFMPEG

INFO = {'width': 1920, 'height': 1080, 'video_index': 0, 'duration': 6.0}
TRACK = {'ok': True, 'shots': [{'start': 0.0, 'end': 6.0, 'layout': 'fill', 'points': [(0.0, 0.4), (6.0, 0.6)]}],
         'face_band': [0.32, 0.48, 0.91]}


class FaceBandTests(unittest.TestCase):
    def test_band_covers_head_and_ignores_a_stray_detection(self):
        boxes = [(0.4, 0.5, 0.1, 0.3)] * 19 + [(0.4, 0.05, 0.1, 0.1)]   # uma detecção solta lá em cima
        head, face, chin = reframe.face_band(boxes, INFO, None)
        self.assertAlmostEqual(head, 0.5 - 0.3 * reframe.HEAD_ABOVE_FACE, places=3)
        self.assertEqual(face, 0.5)
        self.assertAlmostEqual(chin, 0.5 + 0.3 * (1 + reframe.CHIN_BELOW_FACE), places=3)
        self.assertIsNone(reframe.face_band(boxes[:2], INFO, None))

    def test_band_uses_the_area_without_black_bars(self):
        box = {'x': 0, 'y': 108, 'w': 1920, 'h': 864}
        _, face, _ = reframe.face_band([(0.4, 0.5, 0.1, 0.2)] * 5, INFO, box)
        self.assertAlmostEqual(face, (0.5 * 1080 - 108) / 864, places=3)


class AvoidFaceTests(unittest.TestCase):
    W, H, SIZE, MARGIN = 1080, 1920, 84, round(1920 * 0.27)

    def place(self, band, top_safe=subtitles.TOP_SAFE):
        avoid = tuple(round(v * self.H) for v in band)
        return subtitles.placement(self.W, self.H, self.SIZE, self.MARGIN, None, avoid, top_safe)

    def test_usual_spot_when_it_is_clear_of_the_face(self):
        self.assertEqual(self.place([0.05, 0.12, 0.45]), (2, self.MARGIN))

    def test_below_the_chin_when_there_is_room(self):
        self.assertEqual(self.place([0.2, 0.3, 0.65]), (8, round(0.65 * self.H) + round(self.H * subtitles.BAND_GAP)))

    def test_above_the_head_then_over_the_hair(self):
        gap = round(self.H * subtitles.BAND_GAP)
        self.assertEqual(self.place([0.32, 0.48, 0.91]), (2, self.H - round(0.32 * self.H) + gap))
        # Com gancho no topo não cabe acima da cabeça: vai para cima dos olhos, sobre o cabelo.
        self.assertEqual(self.place([0.32, 0.48, 0.91], subtitles.TOP_SAFE_HOOK),
                         (2, self.H - round(0.48 * self.H) + gap))

    def test_subtitle_files_receive_the_position(self):
        lines = [[{'start': 0.0, 'end': 1.0, 'text': 'oi'}]]
        avoid = tuple(round(v * self.H) for v in (0.32, 0.48, 0.91))
        dyn = subtitles.to_ass_dynamic(lines, self.W, self.H, avoid=avoid)
        style = next(l for l in dyn.splitlines() if l.startswith('Style: Dyn,')).split(',')
        self.assertEqual(style[-2], str(self.H - avoid[0] + round(self.H * subtitles.BAND_GAP)))


class FramedFollowTests(unittest.TestCase):
    def test_graph_and_subtitle_band(self):
        graph, size = media.build_filtergraph(INFO, '9:16', 'track_blur', None, None, TRACK)
        self.assertEqual(size, (1080, 1920))
        self.assertIn("crop=w='2*trunc(min(iw,ih*0.8)/2)'", graph)
        self.assertIn("y='clip(0.6150*ih-oh/2,0,ih-oh)'", graph)   # centro da cabeça
        self.assertIn(f'overlay=0:{media.framed_top(1080, 1920)}', graph)
        self.assertIn('boxblur', graph)
        top = media.framed_top(1080, 1920)
        self.assertEqual(media.content_band('9:16', 'track_blur', INFO, (1080, 1920)), (top, top + 1080))
        # Fora da vertical é o seguir rosto comum; sem rosto, fundo desfocado.
        self.assertNotIn('ffs', media.build_filtergraph(INFO, '1:1', 'track_blur', None, None, TRACK)[0])
        self.assertIsNone(media.content_band('1:1', 'track_blur', INFO, (1080, 1080)))
        self.assertIn('boxblur', media.build_filtergraph(INFO, '9:16', 'track_blur', None, None, {'ok': False})[0])

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_graph_runs_in_ffmpeg_with_scenes_without_face(self):
        track = {**TRACK, 'shots': [TRACK['shots'][0] | {'end': 3.0}, {'start': 3.0, 'end': 6.0, 'layout': 'fit', 'points': []}]}
        info = {**INFO, 'width': 640, 'height': 360}
        graph, (w, h) = media.build_filtergraph(info, '9:16', 'track_blur', None, None, track)
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=10:d=6',
                        '-filter_complex', graph, '-map', '[vout]', '-f', 'null', '-'], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
