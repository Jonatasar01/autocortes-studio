"""Segurança visual: legenda sempre dentro do quadro e recorte que não corta rostos."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import reframe, service, subtitles, textfit
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase

PHRASE = [{'start': 0.0, 'end': 0.3, 'text': 'perigos'}, {'start': 0.3, 'end': 0.6, 'text': 'dos'},
          {'start': 0.6, 'end': 1.0, 'text': 'acampamentos'}]


def dialogues(ass: str) -> list[str]:
    return [line.split(',,', 1)[1].split(',,', 1)[-1] for line in ass.splitlines() if line.startswith('Dialogue')]


class SubtitleLayoutTests(unittest.TestCase):
    def test_regression_long_three_word_phrase_is_broken_and_safe(self):
        report = subtitles._new_report()
        ass = subtitles.to_ass_dynamic([PHRASE], 1080, 1920, report=report)
        self.assertIn('WrapStyle: 0', ass)
        for text in dialogues(ass):
            self.assertIn('\\N', text)
            self.assertTrue(text.startswith('{\\q2'))
        self.assertLessEqual(report['max_width_ratio'], subtitles.SAFE_FILL + 1e-6)
        self.assertLessEqual(report['max_rows'], 2)

    def test_short_words_stay_on_one_line(self):
        line = [{'start': 0, 'end': .2, 'text': 'eu'}, {'start': .2, 'end': .4, 'text': 'vi'},
                {'start': .4, 'end': .6, 'text': 'isso'}]
        self.assertTrue(all('\\N' not in t for t in dialogues(subtitles.to_ass_dynamic([line], 1080, 1920))))

    def test_long_single_word_shrinks_but_fits(self):
        report = subtitles._new_report()
        line = [{'start': 0, 'end': 1, 'text': 'inconstitucionalissimamente'}]
        ass = subtitles.to_ass_dynamic([line], 1080, 1920, report=report)
        self.assertIn('\\fs', ass)
        self.assertEqual(report['reduced_events'], 1)
        self.assertLessEqual(report['max_width_ratio'], subtitles.SAFE_FILL + 1e-6)

    def test_worst_case_highlight_and_emphasis_counted(self):
        words = ['ACAMPAMENTOS', 'DOS', 'PERIGOS']
        plain = textfit.plan_rows(words, safe_width=2000, size=84, min_size=60)
        lit = textfit.plan_rows(words, safe_width=2000, size=84, min_size=60, active_scale=1.08,
                                scales=[1.12, 1, 1])
        self.assertGreater(lit.width, plain.width)

    def test_other_resolutions_and_classic_and_hook(self):
        cues = [{'start': 0, 'end': 2, 'text': 'Uma frase relativamente comprida para a legenda clássica'}]
        for w, h in ((720, 1280), (1080, 1080), (1920, 1080)):
            with self.subTest(size=f'{w}x{h}'):
                report = subtitles._new_report()
                subtitles.to_ass_dynamic([PHRASE], w, h, report=report)
                subtitles.to_ass(cues, w, h, report=report,
                                 hook_text='Um gancho bem comprido que precisa caber nas margens do topo do vídeo')
                self.assertLessEqual(report['max_width_ratio'], subtitles.SAFE_FILL + 1e-6)

    def test_accents_are_measured(self):
        m = textfit.load_metrics()
        if m is None:
            self.skipTest('nenhuma fonte disponível')
        self.assertGreater(m.text_width('ÁÇÃO', 80), 0)
        self.assertAlmostEqual(m.text_width('A', 80), m.text_width('Á', 80), delta=m.text_width('A', 80) * 0.2)

    def test_without_font_uses_conservative_estimate(self):
        with mock.patch.object(textfit, 'load_metrics', return_value=None), \
                mock.patch.object(textfit, 'metrics_for_family', return_value=None):
            report = subtitles._new_report()
            ass = subtitles.to_ass_dynamic([PHRASE], 1080, 1920, report=report)
        self.assertIn('\\N', ass)
        self.assertLessEqual(report['max_width_ratio'], subtitles.SAFE_FILL + 1e-6)


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class LibassRenderTests(unittest.TestCase):
    """Renderiza a legenda com o mesmo libass da exportação e confere os pixels."""

    def ink_columns(self, ass: str, w: int, h: int) -> tuple[int, int]:
        tmp = Path(tempfile.mkdtemp(prefix='libass-'))
        (tmp / 'l.ass').write_text(ass, encoding='utf-8')
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=black:s={w}x{h}:d=0.6',
                              '-vf', 'ass=l.ass,format=gray', '-ss', '0.1', '-frames:v', '1', '-f', 'rawvideo', '-'],
                             cwd=tmp, capture_output=True, check=True).stdout
        cols = [x for x in range(w) if any(raw[y * w + x] > 40 for y in range(0, h, 3))]
        return (cols[0], cols[-1]) if cols else (-1, -1)

    def test_long_phrase_never_touches_frame_edges(self):
        w, h = 1080, 1920
        ass = subtitles.to_ass_dynamic([PHRASE], w, h)
        left, right = self.ink_columns(ass, w, h)
        self.assertGreaterEqual(left, 0)
        margin = subtitles.style_for(w, h)['margin_h']
        self.assertGreaterEqual(left, margin * 0.5)
        self.assertLessEqual(right, w - margin * 0.5)

    def test_prediction_is_not_narrower_than_rendering(self):
        m = textfit.metrics_for_family('Arial')
        if m is None:
            self.skipTest('nenhuma fonte disponível')
        w, h = 2400, 300
        ass = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 2400\nPlayResY: 300\nWrapStyle: 2\n'
               'ScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, '
               'SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, '
               'Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
               'Style: T,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n'
               '\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
               'Dialogue: 0,0:00:00.00,0:00:01.00,T,,0,0,0,,PERIGOS DOS ACAMPAMENTOS\n')
        left, right = self.ink_columns(ass, w, h)
        rendered = right - left + 1
        predicted = m.text_width('PERIGOS DOS ACAMPAMENTOS', 84)
        # Fonte do sistema diferente da medida (ex.: Linux sem Arial) só torna a previsão mais larga.
        self.assertLessEqual(rendered, predicted * 1.02)


class FaceSafeCropTests(unittest.TestCase):
    CROP = 0.3167  # 1920×1080 → 9:16

    def test_face_near_left_edge_has_safe_center(self):
        box = (0.01, 0.20, 0.18, 0.30)
        lo, hi = reframe.safe_center_interval(box, self.CROP)
        self.assertGreaterEqual(reframe.face_visibility(box, (lo + hi) / 2, self.CROP), 0.999)

    def test_face_near_right_edge_is_symmetric(self):
        box = (1 - 0.01 - 0.18, 0.20, 0.18, 0.30)
        lo, hi = reframe.safe_center_interval(box, self.CROP)
        self.assertGreaterEqual(reframe.face_visibility(box, (lo + hi) / 2, self.CROP), 0.999)

    def test_face_too_wide_has_no_safe_crop(self):
        self.assertIsNone(reframe.safe_center_interval((0.10, 0.10, 0.55, 0.70), self.CROP))

    def test_two_faces_close_keep_both_far_keep_speaker(self):
        a, near, far = (0.40, 0.2, 0.08, 0.15), (0.52, 0.2, 0.08, 0.15), (0.85, 0.2, 0.08, 0.15)
        both = reframe._sample_interval((0, 0.44, a, [a, near]), self.CROP)
        center = (both[0] + both[1]) / 2
        self.assertGreaterEqual(reframe.face_visibility(near, center, self.CROP), 0.999)
        only = reframe._sample_interval((0, 0.44, a, [a, far]), self.CROP)
        self.assertEqual(only, reframe.safe_center_interval(a, self.CROP))

    def test_camera_keeps_face_visible_through_fast_move(self):
        # O rosto salta da esquerda para a direita; a suavização sozinha deixaria a câmera para trás.
        left, right = (0.05, 0.2, 0.12, 0.2), (0.80, 0.2, 0.12, 0.2)
        targets = [(i / 5, left[0] + left[2] / 2, left, [left]) for i in range(10)]
        targets += [(2 + i / 5, right[0] + right[2] / 2, right, [right]) for i in range(10)]
        path = reframe.camera_path(targets, self.CROP)
        vis = reframe.visibility_report(targets, path, self.CROP)
        self.assertGreaterEqual(min(vis), 0.999)

    def test_legacy_targets_without_boxes_still_work(self):
        path = reframe.camera_path([(i / 5, 0.5) for i in range(10)], crop_frac=0.3)
        self.assertTrue(all(abs(x - 0.5) < 1e-6 for _, x in path))


class SpeakerChoiceTests(unittest.TestCase):
    """Quem fala: falsos positivos que piscam saem; textura que se mexe inteira não vence uma boca."""

    def setUp(self):
        try:
            import cv2  # noqa: F401
            import numpy as np
        except ImportError:
            self.skipTest('OpenCV ausente')
        self.np = np
        self.rng = np.random.default_rng(7)

    def frames(self, n, face, texture=None, flicker_at=None):
        np = self.np
        samples = []
        base = self.rng.integers(0, 255, (180, 320), dtype='uint8')
        for i in range(n):
            gray = base.copy()
            fx, fy, fw, fh = face
            # boca (65–95% da altura do rosto) muda a cada quadro; o resto do rosto fica parado
            y0, y1 = int((fy + fh * 0.65) * 180), int((fy + fh * 0.95) * 180)
            gray[y0:y1, int(fx * 320):int((fx + fw) * 320)] = self.rng.integers(0, 255, (y1 - y0, int(fw * 320)))
            faces = [face]
            if texture:
                tx, ty, tw, th = texture   # textura: a caixa inteira muda (como papel de parede com a câmera andando)
                ys, xs = slice(int(ty * 180), int((ty + th) * 180)), slice(int(tx * 320), int((tx + tw) * 320))
                gray[ys, xs] = self.rng.integers(0, 255, gray[ys, xs].shape)
                faces.append(texture)
            if flicker_at is not None and i == flicker_at:
                faces.append((0.05, 0.05, 0.1, 0.18))
            samples.append({'t': i / 5, 'gray': np.ascontiguousarray(gray, dtype='uint8'), 'faces': faces})
        return samples

    def test_textured_false_face_does_not_win(self):
        face, texture = (0.55, 0.3, 0.12, 0.22), (0.1, 0.25, 0.2, 0.36)   # textura maior que o rosto
        targets = reframe.speaker_targets(self.frames(20, face, texture))
        late = [tg[2] for tg in targets[12:]]   # depois da espera mínima de 2 s no falante
        self.assertTrue(all(box == face for box in late), late)

    def test_static_game_icon_loses_to_moving_real_face(self):
        # Live de jogo: ícone de agente parado (com animação logo abaixo, que parece "boca") e o rosto real da
        # webcam, que se mexe. O ícone tem a boca mais "ativa", mas é imagem parada: o rosto real vence.
        np = self.np
        icon = (0.6, 0.6, 0.1, 0.18)
        samples = []
        base = self.rng.integers(0, 255, (180, 320), dtype='uint8')
        for i in range(25):
            gray = base.copy()
            y0, y1 = int((0.6 + 0.18 * 0.65) * 180), int((0.6 + 0.18 * 0.95) * 180)
            gray[y0:y1, 192:224] = self.rng.integers(0, 255, (y1 - y0, 32))
            dx, dy = 0.02 * np.sin(i * 1.3), 0.015 * np.cos(i * 0.9)
            real = (round(0.15 + dx, 4), round(0.2 + dy, 4), round(0.08 + 0.006 * np.sin(i), 4), 0.15)
            samples.append({'t': i / 5, 'gray': np.ascontiguousarray(gray), 'faces': [real, icon]})
        targets = reframe.speaker_targets(samples)
        self.assertTrue(all(tg[2] != icon for tg in targets))
        self.assertTrue(all(icon not in tg[3] for tg in targets))   # nem entra no enquadramento conjunto

    def test_flickering_detection_is_dropped(self):
        face = (0.55, 0.3, 0.12, 0.22)
        targets = reframe.speaker_targets(self.frames(12, face, flicker_at=5))
        self.assertEqual(targets[5][3], [face])
        self.assertTrue(all(tg[2] == face for tg in targets))


class YuNetTests(unittest.TestCase):
    def test_model_is_bundled_and_ignores_texture(self):
        if not reframe.yunet_available():
            self.skipTest('OpenCV sem FaceDetectorYN')
        import numpy as np
        frame = np.random.default_rng(3).integers(0, 255, (360, 640, 3), dtype='uint8')
        boxes, marks = reframe.detect_faces_yunet(frame, reframe._yunet((640, 360)))
        self.assertEqual((boxes, marks), ([], []))

    def test_regions_follow_landmarks(self):
        box = (0.4, 0.2, 0.2, 0.3)
        marks = ((0.45, 0.3), (0.55, 0.3), (0.5, 0.36), (0.46, 0.42), (0.54, 0.42))
        mouth, forehead = reframe.face_regions(box, marks)
        self.assertLess(mouth[1], 0.42 < mouth[1] + mouth[3])     # contém a linha da boca
        self.assertLess(forehead[1] + forehead[3], 0.3)            # testa acima dos olhos


class PlanFallbackTests(unittest.TestCase):
    def fake_samples(self, box):
        import numpy as np
        gray = np.zeros((90, 160), dtype='uint8')
        hist = np.ones((32, 32), dtype='float32')
        return [{'t': i / 5, 'hist': hist, 'gray': gray, 'faces': [box]} for i in range(30)]

    def run_plan(self, box):
        media = {'width': 1920, 'height': 1080}
        with mock.patch.object(reframe, 'available', return_value=True), \
                mock.patch.object(reframe, 'sample', return_value=self.fake_samples(box)):
            return reframe.plan(Path('x.mp4'), media, 0, 6, (1080, 1920))

    def test_oversized_face_falls_back_to_full_frame(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('numpy ausente')
        result = self.run_plan((0.10, 0.10, 0.55, 0.70))
        self.assertFalse(result['ok'])
        self.assertIn('não cabem', result['message'])

    def test_edge_face_is_tracked_safely(self):
        try:
            import numpy  # noqa: F401
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest('OpenCV ausente')
        result = self.run_plan((0.01, 0.20, 0.15, 0.30))
        self.assertTrue(result['ok'], result['message'])
        self.assertGreaterEqual(result['metrics']['face_visibility_min'], 0.98)
        self.assertEqual(result['metrics']['unsafe_samples'], 0)


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class ExportQualityRecordTests(ServiceBase):
    def test_export_records_subtitle_quality(self):
        video = make_video(self.tmp / 'qualidade.mp4', size='1280x720', duration=20, fps=25)
        res = self.create(video)
        clip = service.get_project(res['id'])['clips'][0]
        job = service.request_export(res['id'], clip['id'], {'format': '9:16', 'framing': 'blur', 'subtitles': True,
                                                             'subtitle_style': 'dinamica', 'hook': True})
        done = service.manager.wait(job['id'], 240)
        self.assertEqual(done['status'], 'done', done.get('error'))
        record = service.get_project(res['id'])['clips'][0]['exports']['9:16']
        quality = record['quality']['subtitles']
        self.assertGreater(quality['events'], 0)
        self.assertLessEqual(quality['max_width_ratio'], subtitles.SAFE_FILL + 1e-6)
        self.assertIsNone(record['quality']['reframe'])  # fundo desfocado: sem recorte de rosto


if __name__ == '__main__':
    unittest.main()
