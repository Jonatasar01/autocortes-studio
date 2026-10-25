"""Expressões longas do FFmpeg (recorte que segue o rosto, cortes de pausa, zoom) não podem passar do limite de profundidade."""
from __future__ import annotations

import subprocess
import unittest

from app import media, reframe
from tests.helpers import HAS_FFMPEG


def _depth(expr: str) -> int:
    depth = best = 0
    for ch in expr:
        depth += ch == '('
        depth -= ch == ')'
        best = max(best, depth)
    return best


class ExprSumTests(unittest.TestCase):
    def test_balanced(self):
        self.assertEqual(media.expr_sum([]), '0')
        self.assertEqual(media.expr_sum(['a']), 'a')
        self.assertEqual(media.expr_sum(['a', 'b', 'c']), '(a+(b+c))')
        self.assertLessEqual(_depth(media.expr_sum(['x'] * 1000)), 11)

    def _long_shots(self):
        pts = [[i * 0.2, 0.3 + 0.4 * (i % 2)] for i in range(300)]
        return [{'start': 0.0, 'end': 61.0, 'layout': 'fill', 'points': pts},
                {'start': 61.0, 'end': 62.0, 'layout': 'fit', 'points': []}]

    def test_crop_expression_many_points_value(self):
        expr = reframe.crop_x_expression(self._long_shots())
        self.assertGreater(expr.count('gte('), 300)
        self.assertLess(_depth(expr), 20)

    @unittest.skipUnless(HAS_FFMPEG, 'ffmpeg ausente')
    def test_ffmpeg_accepts_long_crop_expression(self):
        x = reframe.crop_x_expression(self._long_shots())
        vf = f"crop=w=90:h=160:x='clip(({x})*iw-ow/2,0,iw-ow)':y=0"
        r = subprocess.run(['ffmpeg', '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=s=320x160:d=0.2',
                            '-vf', vf, '-f', 'null', '-'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])


if __name__ == '__main__':
    unittest.main()
