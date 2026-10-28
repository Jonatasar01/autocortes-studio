"""Cena principal em transmissões ("só o streamer" sem janela de webcam): rostos só na bancada dos streamers;
campo, túnel ou jogo em tela cheia ficam inteiros com fundo desfocado."""
from __future__ import annotations

import unittest

import numpy as np

from app import reframe


def hist(peak: int, spread: int = 2) -> np.ndarray:
    """Histograma H×S 32×32 concentrado numa cor (cada cena tem a sua paleta)."""
    h = np.zeros((32, 32), dtype=np.float32)
    h[max(0, peak - spread):peak + spread, 8:24] = 1.0
    return h / np.linalg.norm(h)


def samples(plan: list[tuple[float, int, float, int]]) -> list[dict]:
    """``[(segundos, cor, largura_do_rosto, nº_de_rostos), ...]`` → amostras a 5 por segundo."""
    out, t = [], 0.0
    for seconds, color, face_w, count in plan:
        for _ in range(round(seconds * reframe.SAMPLE_FPS)):
            faces = [(0.1 + 0.12 * k, 0.3, face_w, face_w * 1.6) for k in range(count)]
            out.append({'t': round(t, 2), 'hist': hist(color), 'faces': faces})
            t += 1 / reframe.SAMPLE_FPS
    return out


BOOTH, FIELD, TUNNEL = 4, 20, 28


class MainSetTests(unittest.TestCase):
    def test_booth_is_found_between_field_and_tunnel(self):
        s = samples([(12, TUNNEL, 0.055, 8), (40, BOOTH, 0.09, 4), (11, FIELD, 0.05, 7), (15, BOOTH, 0.10, 3),
                     (20, FIELD, 0.05, 7)])
        mask = reframe.main_set_mask(s)
        self.assertIsNotNone(mask)
        on = [x['t'] for x, m in zip(s, mask) if m]
        self.assertAlmostEqual(min(on), 12.0, delta=0.5)
        self.assertTrue(all(12 <= t < 52 or 63 <= t < 78 for t in on))
        self.assertAlmostEqual(sum(mask) / reframe.SAMPLE_FPS, 55, delta=1)

    def test_single_scene_changes_nothing(self):
        # Podcast ou live com uma câmera só: não há outra cena para separar.
        self.assertIsNone(reframe.main_set_mask(samples([(60, BOOTH, 0.09, 2)])))

    def test_main_scene_is_the_one_with_close_faces_not_the_longest(self):
        # Campo ocupa mais tempo, mas com rostos pequenos; a bancada (close) é a principal.
        s = samples([(45, FIELD, 0.04, 9), (25, BOOTH, 0.11, 3)])
        mask = reframe.main_set_mask(s)
        self.assertTrue(all(m == (x['hist'][BOOTH, 10] > 0) for x, m in zip(s, mask)))

    def test_short_flash_inherits_the_surrounding_scene(self):
        s = samples([(20, BOOTH, 0.09, 3), (0.4, FIELD, 0.05, 6), (20, BOOTH, 0.09, 3), (15, FIELD, 0.05, 6)])
        mask = reframe.main_set_mask(s)
        flash = [m for x, m in zip(s, mask) if 20 <= x['t'] < 20.4]
        self.assertTrue(all(flash))

    def test_too_few_samples(self):
        self.assertIsNone(reframe.main_set_mask(samples([(2, BOOTH, 0.09, 2), (1, FIELD, 0.05, 3)])))


class CacheKeyTests(unittest.TestCase):
    def test_streamer_and_track_plans_are_cached_apart(self):
        from pathlib import Path
        from unittest import mock
        import tempfile
        from app import pipeline
        folder = Path(tempfile.mkdtemp(prefix='plano-'))
        p = {'filename': 'v.mp4', 'scenes': []}
        info = {'width': 1920, 'height': 1080, 'duration': 60.0}
        calls = []

        def fake_plan(*a, main_set_only=False, **k):
            calls.append(main_set_only)
            return {'ok': True, 'shots': [], 'main_set_only': main_set_only}

        with mock.patch.object(pipeline.reframe, 'plan', side_effect=fake_plan):
            ctx = mock.Mock(cancel_event=None)
            a = pipeline._cached_track(folder, p, info, 0, 30, '9:16', ctx)
            b = pipeline._cached_track(folder, p, info, 0, 30, '9:16', ctx, main_set_only=True)
            pipeline._cached_track(folder, p, info, 0, 30, '9:16', ctx, main_set_only=True)   # do cache
        self.assertEqual(calls, [False, True])
        self.assertFalse(a['main_set_only'])
        self.assertTrue(b['main_set_only'])


if __name__ == '__main__':
    unittest.main()
