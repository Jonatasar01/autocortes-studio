"""Filtros de cor: catálogo, cadeia do FFmpeg e aplicação depois do layout, antes da legenda."""
from __future__ import annotations

import unittest

from app import filters, media
from app.errors import AppError

INFO = {'width': 1920, 'height': 1080, 'video_index': 0, 'fps': 30, 'duration': 10.0}


class FilterTests(unittest.TestCase):
    def test_catalog_starts_with_original_and_has_css(self):
        cat = filters.catalog()
        self.assertEqual(cat[0]['id'], 'nenhum')
        self.assertTrue(all(f['css'] for f in cat[1:]))

    def test_chain(self):
        self.assertEqual(filters.chain(None), '')
        self.assertEqual(filters.chain('nenhum'), '')
        self.assertTrue(filters.chain('pb').startswith(',hue=s=0'))
        with self.assertRaises(AppError):
            filters.chain('../../etc')

    def test_filter_goes_before_the_subtitles(self):
        graph, _ = media.build_filtergraph(INFO, '9:16', 'blur', 'legenda.ass', post=filters.chain('neon'))
        self.assertLess(graph.index('saturation=1.65'), graph.index('ass=legenda.ass'))


if __name__ == '__main__':
    unittest.main()
