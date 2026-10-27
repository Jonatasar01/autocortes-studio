"""A legenda não pode ficar em cima da borda do vídeo nos enquadramentos com barras ou fundo desfocado."""
from __future__ import annotations

import re
import unittest

from app import media, subtitles

INFO = {'width': 1920, 'height': 1080, 'video_index': 0}


def style_line(ass: str, name: str) -> list[str]:
    line = next(l for l in ass.splitlines() if l.startswith(f'Style: {name},'))
    return line.split(',')


class BandTests(unittest.TestCase):
    def test_band_for_blur_and_fit_only(self):
        self.assertEqual(media.content_band('9:16', 'blur', INFO, (1080, 1920)), (656, 1264))
        letterboxed = {**INFO, 'letterbox': {'w': 1920, 'h': 818, 'x': 0, 'y': 131}}
        self.assertEqual(media.content_band('9:16', 'fit', letterboxed, (1080, 1920)), (730, 1190))
        self.assertIsNone(media.content_band('9:16', 'track', INFO, (1080, 1920)))
        self.assertIsNone(media.content_band('original', 'blur', INFO, (1920, 1080)))

    def test_below_video_when_there_is_room(self):
        align, margin = subtitles.placement(1080, 1920, 84, 518, (730, 1190))
        self.assertEqual(align, 8)               # ancorada no topo
        self.assertGreater(margin, 1190)         # começa abaixo do vídeo
        self.assertLessEqual(margin + 2 * 84 * 1.25, 1920 * (1 - subtitles.UI_BOTTOM_PORTRAIT))

    def test_inside_video_when_band_is_tall(self):
        align, margin = subtitles.placement(1080, 1920, 84, 518, (160, 1650))
        self.assertEqual(align, 2)
        self.assertGreater(1920 - margin, 160)   # base da legenda dentro do vídeo
        self.assertLess(1920 - margin, 1650)

    def test_full_frame_keeps_default(self):
        self.assertEqual(subtitles.placement(1080, 1920, 84, 518, None), (2, 518))
        self.assertEqual(subtitles.placement(1080, 1920, 84, 518, (0, 1920)), (2, 518))

    def test_styles_use_the_band(self):
        lines = [[{'start': 0.0, 'end': 0.5, 'text': 'calma'}, {'start': 0.5, 'end': 1.0, 'text': 'calma'}]]
        dyn = subtitles.to_ass_dynamic(lines, 1080, 1920, video_band=(730, 1190))
        fields = style_line(dyn, 'Dyn')
        self.assertEqual(fields[18], '8')
        self.assertGreater(int(fields[21]), 1190)
        classic = subtitles.to_ass([{'start': 0, 'end': 1, 'text': 'Calma, calma.'}], 1080, 1920,
                                   video_band=(730, 1190))
        self.assertEqual(style_line(classic, 'Default')[18], '8')
        plain = subtitles.to_ass_dynamic(lines, 1080, 1920)
        self.assertEqual(style_line(plain, 'Dyn')[18], '2')
        self.assertTrue(re.search(r'Dialogue: .*CALMA', dyn))


if __name__ == '__main__':
    unittest.main()
