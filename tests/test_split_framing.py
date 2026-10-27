"""Enquadramento "Webcam + jogo": janela da webcam em cima, jogo embaixo (tela dividida)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app import direction, media
from tests.helpers import HAS_FFMPEG, make_video

INFO = {'width': 1920, 'height': 1080, 'video_index': 0, 'fps': 30, 'duration': 10.0}
CAM_LEFT = [0.0, 0.2, 0.31, 0.35]   # webcam no canto esquerdo, como nas lives de jogo


class SplitLayoutTests(unittest.TestCase):
    def test_webcam_band_height_is_clamped(self):
        top = media.split_top_height(1080, 1920, INFO, CAM_LEFT)
        self.assertTrue(1920 * 0.28 <= top <= 1920 * 0.42, top)
        self.assertEqual(top % 2, 0)
        wide = media.split_top_height(1080, 1920, INFO, [0.0, 0.0, 1.0, 0.1])
        self.assertEqual(wide, media._even(1920 * 0.28))

    def test_game_crop_skips_the_webcam_window(self):
        top = media.split_top_height(1080, 1920, INFO, CAM_LEFT)
        left = media.split_game_left(1080, 1920, INFO, CAM_LEFT, top)
        self.assertGreaterEqual(left, 0.31)        # começa depois da webcam
        right_cam = [0.69, 0.2, 0.31, 0.35]
        left_r = media.split_game_left(1080, 1920, INFO, right_cam, top)
        cw = 1080 * 1080 / (1920 - top) / 1920
        self.assertLessEqual(left_r + cw, 0.69 + 1e-3)   # termina antes da webcam
        center_cam = [0.4, 0.0, 0.2, 0.3]
        self.assertAlmostEqual(media.split_game_left(1080, 1920, INFO, center_cam, top), (1 - cw) / 2, places=3)

    def test_filtergraph_stacks_webcam_over_game(self):
        graph, size = media.build_filtergraph(INFO, '9:16', 'split', None, track={'ok': True, 'facecam': CAM_LEFT,
                                                                               'shots': []})
        self.assertEqual(size, (1080, 1920))
        self.assertIn('vstack=inputs=2', graph)
        self.assertIn('crop=w=iw*0.3100', graph)

    def test_without_webcam_or_in_landscape_it_falls_back(self):
        graph, _ = media.build_filtergraph(INFO, '9:16', 'split', None, track=None)
        self.assertNotIn('vstack', graph)          # sem webcam: fundo desfocado
        graph, _ = media.build_filtergraph(INFO, '16:9', 'split', None, track={'ok': False, 'facecam': CAM_LEFT,
                                                                              'shots': []})
        self.assertNotIn('vstack', graph)          # tela dividida só no vertical

    def test_subtitles_sit_right_below_the_webcam(self):
        band = media.content_band('9:16', 'split', INFO, (1080, 1920), CAM_LEFT)
        self.assertEqual(band, (0, media.split_top_height(1080, 1920, INFO, CAM_LEFT)))

    def test_live_de_jogo_style_uses_split_without_zoom(self):
        self.assertEqual(direction.PRESETS['live_jogo']['framing'], 'split')
        self.assertEqual(direction.PRESETS['live_jogo']['zoom'], 0.0)
        self.assertIn('live_jogo', direction.STYLES)

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_ffmpeg_renders_the_split(self):
        tmp = Path(tempfile.mkdtemp(prefix='split-'))
        src = make_video(tmp / 'live.mp4', size='640x360', duration=2)
        info = media.probe_media(src)
        graph, (W, H) = media.build_filtergraph(info, '9:16', 'split', None,
                                                track={'ok': True, 'facecam': CAM_LEFT, 'shots': []})
        out = tmp / 'out.mp4'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', str(src), '-filter_complex', graph,
                        '-map', '[vout]', '-t', '1', '-c:v', 'libx264', '-preset', 'ultrafast', str(out)], check=True)
        probe = media.probe_media(out)
        self.assertEqual((probe['width'], probe['height']), (W, H))


if __name__ == '__main__':
    unittest.main()
