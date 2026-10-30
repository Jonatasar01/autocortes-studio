"""Aparência da legenda escolhida na aba Legendas: fonte, tamanho, cores, fundo e letras."""
from __future__ import annotations

import re
import unittest

from app import service, storage, subtitles
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase

CUES = [{'start': 0.0, 'end': 2.0, 'text': 'Que estilo legal, mano'}]
LINES = [[{'start': 0.0, 'end': 0.5, 'text': 'que'}, {'start': 0.5, 'end': 1.0, 'text': 'estilo'},
          {'start': 1.0, 'end': 1.6, 'text': 'legal'}]]


def style_line(ass: str, name: str) -> list[str]:
    line = next(l for l in ass.splitlines() if l.startswith(f'Style: {name},'))
    return line[len('Style: '):].split(',')


class SubtitleLookTests(unittest.TestCase):
    def test_default_look_is_the_usual_subtitle(self):
        classic = style_line(subtitles.to_ass(CUES, 1080, 1920), 'Default')
        self.assertEqual(classic[1:8], ['Arial', '67', '&H00FFFFFF', '&H00FFFFFF', '&H00000000', '&H64000000', '-1'])
        self.assertEqual(classic[15], '1')   # contorno, sem caixa
        dyn = subtitles.to_ass_dynamic(LINES, 1080, 1920)
        self.assertEqual(style_line(dyn, 'Dyn')[1:8],
                         ['Arial', '84', '&H00FFFFFF', '&H00FFFFFF', '&H00000000', '&H80000000', '-1'])
        self.assertIn('{\\c&H0027F804&', dyn)   # palavra falada em verde
        self.assertIn('ESTILO', dyn)
        self.assertEqual(subtitles.to_ass(CUES, 1080, 1920, look=subtitles.LOOK_DEFAULT),
                         subtitles.to_ass(CUES, 1080, 1920))

    def test_font_colors_size_and_box(self):
        look = {'font': 'Impact', 'size': 'g', 'color': '#ffe000', 'highlight': '#FF3B30', 'outline': '#112233',
                'background': 'caixa', 'case': 'auto'}
        dyn = subtitles.to_ass_dynamic(LINES, 1080, 1920, look=look)
        st = style_line(dyn, 'Dyn')
        self.assertEqual(st[1:4], ['Impact', str(round(1080 * 0.078 * 1.18)), '&H0000E0FF'])
        self.assertEqual(st[5], '&H26332211')        # caixa na cor escolhida, um pouco transparente
        self.assertEqual((st[7], st[15]), ('0', '3'))   # Impact sem negrito sintético; BorderStyle 3 = caixa
        self.assertIn('{\\c&H00303BFF&', dyn)        # palavra falada em vermelho
        self.assertIn('Style: Hook,Impact,', dyn)    # o gancho acompanha a fonte

    def test_without_outline(self):
        for ass, name in ((subtitles.to_ass(CUES, 1080, 1920, look={'background': 'nenhum'}), 'Default'),
                          (subtitles.to_ass_dynamic(LINES, 1080, 1920, look={'background': 'nenhum'}), 'Dyn')):
            with self.subTest(style=name):
                st = style_line(ass, name)
                self.assertEqual(st[15:18], ['1', '0', '0'])   # BorderStyle 1, sem contorno, sem sombra

    def test_letter_case(self):
        normal = subtitles.to_ass_dynamic(LINES, 1080, 1920, look={'case': 'normal'})
        self.assertIn('estilo', normal)
        self.assertNotIn('ESTILO', normal)
        upper = subtitles.to_ass(CUES, 1080, 1920, look={'case': 'upper'})
        self.assertIn('QUE ESTILO LEGAL', upper)
        self.assertIn('Que estilo legal', subtitles.to_ass(CUES, 1080, 1920))

    def test_invalid_values_fall_back_to_the_default(self):
        bad = {'font': 'Wingdings', 'size': 'xl', 'color': 'red', 'highlight': '#12345', 'outline': '#GGGGGG',
               'background': 'neon', 'case': 'title'}
        self.assertEqual(subtitles.clean_look(bad), subtitles.LOOK_DEFAULT)
        self.assertEqual(subtitles.clean_look(None), subtitles.LOOK_DEFAULT)
        self.assertEqual(subtitles.clean_look({'color': '#abcdef'})['color'], '#ABCDEF')

    def test_every_offered_font_has_width_metrics(self):
        # Sem as métricas, a quebra de linha seria medida com outra fonte e o texto poderia sair da tela.
        from app import textfit
        for font in subtitles.SUBTITLE_FONTS:
            with self.subTest(font=font):
                self.assertIn(font.lower(), textfit.FAMILY_FILES)

    def test_bundled_fonts_ship_with_the_app(self):
        # Montserrat, Anton, Bebas Neue e Poppins vêm em app/fonts: o vídeo não depende do Windows ter a fonte.
        from app import textfit
        for font in ('Montserrat ExtraBold', 'Montserrat Black', 'Anton', 'Bebas Neue', 'Poppins'):
            with self.subTest(font=font):
                path = textfit.APP_FONTS / subtitles.SUBTITLE_FONTS[font][0]
                self.assertEqual(path.read_bytes()[:4], b'\x00\x01\x00\x00')
                self.assertEqual(textfit.metrics_for_family(font), textfit.load_metrics(str(path)))

    def test_every_new_phrase_pops_in(self):
        dyn = subtitles.to_ass_dynamic(LINES + [[{'start': 2.0, 'end': 2.5, 'text': 'mano'}]], 1080, 1920)
        events = [l for l in dyn.splitlines() if l.startswith('Dialogue: 0,')]
        self.assertEqual([subtitles.POP_ANIM in e for e in events], [True, False, False, True])


class SubtitleLookApiTests(unittest.TestCase):
    def test_export_options_validate_the_look(self):
        from pydantic import ValidationError
        from app.main import ExportOptions
        ok = ExportOptions(subtitle_look={'font': 'Verdana', 'color': '#00ff88'}).model_dump()['subtitle_look']
        self.assertEqual((ok['font'], ok['color'], ok['size']), ('Verdana', '#00ff88', 'm'))
        for bad in ({'font': 'Papyrus'}, {'color': 'blue'}, {'outline': '#0000'}, {'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                ExportOptions(subtitle_look=bad)
        self.assertIsNone(ExportOptions().subtitle_look)
        self.assertEqual(ExportOptions(subtitle_look={'font': 'Bebas Neue'}).subtitle_look.font, 'Bebas Neue')
        self.assertTrue(re.fullmatch(r'#[0-9A-F]{6}', subtitles.LOOK_DEFAULT['highlight']))


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class SubtitleLookExportTests(ServiceBase):
    def test_only_a_changed_look_goes_to_the_export(self):
        pid = self.create(make_video(self.tmp / 'look.mp4', duration=6))['id']
        cid = storage.read_project(pid)['clips'][0]['id']
        base = {'format': '16:9', 'framing': 'fit', 'subtitles': True, 'refine_subtitles': False}
        plain = service.request_export(pid, cid, {**base, 'subtitle_look': dict(subtitles.LOOK_DEFAULT)})
        self.assertNotIn('subtitle_look', service.manager.get(plain['id'])['params'])
        styled = service.request_export(pid, cid, {**base, 'subtitle_look': {'font': 'Georgia', 'color': '#ff00aa'}})
        params = service.manager.get(styled['id'])['params']
        self.assertEqual((params['subtitle_look']['font'], params['subtitle_look']['color']), ('Georgia', '#FF00AA'))
        for job in (plain, styled):
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')


if __name__ == '__main__':
    unittest.main()
