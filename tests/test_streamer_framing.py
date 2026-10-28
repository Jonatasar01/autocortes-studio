"""Só o streamer: acha a janela da webcam em lives de jogo e a encaixa na tela com fundo desfocado."""
from __future__ import annotations

import unittest

from app import media, reframe

TRACK = {'ok': True, 'shots': [{'start': 0.0, 'end': 6.0, 'layout': 'fill', 'points': [[0.0, 0.5], [6.0, 0.5]]}],
         'facecam': [0.0, 0.075, 0.313, 0.31]}
INFO = {'width': 1920, 'height': 1080, 'video_index': 0, 'duration': 6.0}


class FacecamDetectionTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2  # noqa: F401
            import numpy as np
        except ImportError:
            self.skipTest('OpenCV ausente')
        self.np = np

    def samples(self, framed: bool):
        np = self.np
        rng = np.random.default_rng(5)
        out = []
        for i in range(30):
            gray = rng.integers(0, 255, (270, 480), dtype='uint8')   # "jogo": muda a cada quadro
            if framed:
                gray[20:110, 0:150] = 40 + (i % 3) * 5               # janela da webcam
                gray[20, 0:150] = gray[110, 0:150] = 255             # moldura fixa
                gray[20:110, 150] = 255
            dx = 0.01 * np.sin(i)
            out.append({'t': i / 5, 'gray': gray, 'faces': [(round(0.12 + dx, 4), 0.2, 0.06, 0.12)]})
        return out

    def test_finds_the_window_around_the_face(self):
        s = self.samples(True)
        rect = reframe.detect_facecam(s, reframe.speaker_targets(s))
        self.assertIsNotNone(rect)
        x, y, w, h = rect
        self.assertAlmostEqual(x, 0.0, delta=0.01)
        self.assertAlmostEqual(y, 20 / 270, delta=0.02)
        self.assertAlmostEqual(x + w, 150 / 480, delta=0.02)
        self.assertAlmostEqual(y + h, 110 / 270, delta=0.02)

    def test_no_window_means_none(self):
        s = self.samples(False)
        self.assertIsNone(reframe.detect_facecam(s, reframe.speaker_targets(s)))

    def framed_at(self, x0: int, x1: int, face_x: float, extra_face: float | None = None):
        np = self.np
        rng = np.random.default_rng(9)
        out = []
        for i in range(30):
            gray = rng.integers(0, 255, (270, 480), dtype='uint8')
            gray[20:110, x0:x1] = 40 + (i % 3) * 5
            gray[20, x0:x1] = gray[110, x0:x1] = 255
            gray[20:110, x0] = gray[20:110, x1 - 1] = 255
            faces = [(round(face_x + 0.01 * np.sin(i), 4), 0.2, 0.06, 0.12)]
            if extra_face is not None:
                faces.append((extra_face, 0.5, 0.12, 0.24))
            out.append({'t': i / 5, 'gray': gray, 'faces': faces})
        return out

    def test_window_on_the_right_side(self):
        s = self.framed_at(330, 480, 0.80)
        rect = reframe.detect_facecam(s, reframe.speaker_targets(s))
        self.assertIsNotNone(rect)
        self.assertAlmostEqual(rect[0], 330 / 480, delta=0.02)
        self.assertAlmostEqual(rect[0] + rect[2], 1.0, delta=0.02)

    def test_central_speaker_does_not_hide_the_side_webcam(self):
        # Rosto grande de um jogador no meio da tela (escolhido como quem fala): a janela da esquerda continua achada.
        s = self.framed_at(0, 150, 0.12, extra_face=0.44)
        rect = reframe.detect_facecam(s, [(x['t'], 0.5, (0.44, 0.5, 0.12, 0.24)) for x in s])
        self.assertIsNotNone(rect)
        self.assertLess(rect[0] + rect[2], 0.42)

    def test_window_in_the_middle_is_ignored(self):
        s = self.framed_at(165, 315, 0.47)
        self.assertIsNone(reframe.detect_facecam(s, reframe.speaker_targets(s)))


class StreamerGraphTests(unittest.TestCase):
    def test_crops_the_webcam_with_blurred_background(self):
        graph, size = media.build_filtergraph(INFO, '9:16', 'streamer', None, None, TRACK, '')
        self.assertIn('crop=w=iw*0.3130:h=ih*0.3100:x=iw*0.0000:y=ih*0.0750', graph)
        self.assertIn('boxblur', graph)
        self.assertEqual(size, (1080, 1920))

    def test_without_webcam_follows_the_face(self):
        graph, _ = media.build_filtergraph(INFO, '9:16', 'streamer', None, None, {**TRACK, 'facecam': None}, '')
        self.assertIn("crop=w='2*trunc(", graph)

    def test_band_follows_the_webcam_height(self):
        top, bottom = media.content_band('9:16', 'streamer', INFO, (1080, 1920), TRACK['facecam'])
        # janela de 601x335 px encaixada em 1080 de largura: ~602 px de altura no meio da tela
        self.assertAlmostEqual(bottom - top, round(1080 * (1080 * 0.31) / (1920 * 0.313)), delta=2)
        self.assertIsNone(media.content_band('9:16', 'streamer', INFO, (1080, 1920), None))

    def test_streamer_inside_per_segment_framing(self):
        segs = [{'start': 0, 'end': 3, 'framing': 'streamer'}, {'start': 3, 'end': 6, 'framing': 'blur'}]
        graph, _ = media.build_filtergraph(INFO, '9:16', 'mix', None, None, TRACK, '', segs)
        self.assertIn('crop=w=iw*0.3130', graph)
        self.assertIn("enable='between(t,3.000,6.000)'", graph)

    def test_streamer_segment_without_webcam_uses_the_main_set_plan(self):
        # Sem janela de webcam: o trecho "só o streamer" segue o plano da cena principal (outras cenas em quadro
        # inteiro com fundo desfocado) e o de "seguir rosto" continua com o plano que segue todos os rostos.
        main = {'ok': True, 'main_set_only': True, 'crop_frac': 0.3164,
                'shots': [{'start': 0.0, 'end': 2.0, 'layout': 'fill', 'points': [[0.0, 0.81], [2.0, 0.81]]},
                          {'start': 2.0, 'end': 6.0, 'layout': 'fit', 'points': []}]}
        track = {**TRACK, 'facecam': None, 'crop_frac': 0.3164, 'main_set': main}
        segs = [{'start': 0, 'end': 3, 'framing': 'streamer'}, {'start': 3, 'end': 6, 'framing': 'track'}]
        graph, _ = media.build_filtergraph(INFO, '9:16', 'mix', None, None, track, '', segs)
        self.assertIn('0.81', graph)                 # posição da bancada (plano principal)
        self.assertIn('0.5', graph)                  # plano de seguir rosto
        self.assertIn("between(t,2.000,6.000)", graph)   # cena fora da bancada: fundo desfocado
        only, _ = media.build_filtergraph(INFO, '9:16', 'mix', None, None, track, '',
                                          [{'start': 0, 'end': 6, 'framing': 'streamer'}])
        self.assertIn('0.81', only)
        self.assertNotIn('0.5000', only)


if __name__ == '__main__':
    unittest.main()
