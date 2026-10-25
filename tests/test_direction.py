"""Motor de direção criativa: análise, estilos, plano, limites e renderização com o plano."""
from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from app import direction, media, service
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


def seg(start, end, text, words):
    step = (end - start) / len(words)
    return {'start': start, 'end': end, 'text': text,
            'words': [{'start': round(start + i * step, 3), 'end': round(start + (i + 1) * step - 0.05, 3), 'word': w}
                      for i, w in enumerate(words)]}


def write_wav(path: Path, duration: float, loud: list[tuple[float, float]], rate: int = 16000) -> Path:
    """Tom de 220 Hz baixo, com trechos 12 dB mais altos em ``loud``."""
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(int(duration * rate)):
            t = i / rate
            amp = 0.4 if any(a <= t < b for a, b in loud) else 0.1
            frames += struct.pack('<h', int(amp * 32767 * math.sin(2 * math.pi * 220 * t)))
        wf.writeframes(bytes(frames))
    return path


TRANSCRIPT = [
    seg(0.0, 3.0, 'Hoje eu vou explicar uma coisa incrível.', 'Hoje eu vou explicar uma coisa incrível.'.split()),
    seg(4.5, 7.0, 'Você sabe por quê?', 'Você sabe por quê?'.split()),
    seg(8.5, 11.0, 'Porque funciona demais!', 'Porque funciona demais!'.split()),
    seg(13.0, 16.0, 'Então presta atenção no processo.', 'Então presta atenção no processo.'.split()),
]


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='direcao-'))
        # "incrível" (~2.6 s) e "demais!" (~10.2 s) mais altas.
        cls.wav = write_wav(cls.dir / 'a.wav', 16, [(2.2, 2.95), (9.9, 10.95)])

    def analysis(self, **kw):
        return direction.analyze(TRANSCRIPT, 0.0, 16.0, wav_path=self.wav, **kw)

    def test_measures_emphasis_pauses_and_questions(self):
        a = self.analysis(scenes=[12.0])
        words = {e['text'] for e in a['emphasis']}
        self.assertIn('incrível.', words)
        self.assertIn('demais!', words)
        self.assertNotIn('eu', words)
        self.assertTrue(any(p['after_question'] for p in a['pauses']))
        self.assertEqual(a['scene_cuts'], [12.0])
        self.assertEqual(a['questions'], 1)

    def test_without_transcript_reports_limitation(self):
        a = direction.analyze([], 0, 20)
        self.assertEqual(a['content_type'], 'desconhecido')
        self.assertTrue(a['limitations'])
        style, why = direction.choose_style(a)
        self.assertEqual(style, 'equilibrado')
        plan = direction.build_plan(a, 'auto')
        self.assertEqual([x for x in plan['actions'] if x['type'] in ('zoom', 'cut_pause', 'highlight')], [])

    def test_style_selection(self):
        humor = [seg(0, 2, 'kkkk que isso', ['kkkk', 'que', 'isso'])]
        self.assertEqual(direction.choose_style(direction.analyze(humor, 0, 5))[0], 'impacto')
        talk = [seg(i * 3, i * 3 + 2, 'Como assim?', ['Como', 'assim?']) for i in range(3)]
        self.assertEqual(direction.choose_style(direction.analyze(talk, 0, 10))[0], 'podcast')
        slow = [seg(0, 10, 'era uma vez', ['era', 'uma', 'vez'])]
        self.assertEqual(direction.choose_style(direction.analyze(slow, 0, 20))[0], 'cinematografico')


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='plano-'))
        wav = write_wav(cls.dir / 'a.wav', 16, [(2.2, 2.95), (9.9, 10.95)])
        cls.a = direction.analyze(TRANSCRIPT, 0.0, 16.0, wav_path=wav, scenes=[2.8])

    def test_every_action_has_reason_and_limits(self):
        plan = direction.build_plan(self.a, 'impacto', 1.0)
        self.assertTrue(plan['actions'])
        for act in plan['actions']:
            self.assertTrue(act['reason'])
            self.assertTrue(act['enabled'])
            self.assertLess(act['start'], act['end'])
            if act['type'] == 'zoom':
                self.assertLessEqual(act['params']['amount'], direction.ZOOM_MAX)
                # Não atravessa a mudança de cena em 2.8 s.
                self.assertFalse(act['start'] < 2.8 < act['end'])

    def test_pause_after_question_is_preserved(self):
        plan = direction.build_plan(self.a, 'impacto', 1.0)
        cuts = [x for x in plan['actions'] if x['type'] == 'cut_pause']
        self.assertTrue(cuts)
        # A pausa depois de "quê?" (7.0 → 8.5) é dramática e fica.
        self.assertFalse(any(c['start'] < 8.0 < c['end'] for c in cuts))

    def test_toggles_and_intensity(self):
        off = direction.build_plan(self.a, 'impacto', 1.0, {'zoom': False, 'remove_pauses': False,
                                                              'dynamic_subtitles': False})
        self.assertEqual(off['actions'], [])
        self.assertEqual(off['settings']['subtitle_style'], 'classica')
        zero = direction.build_plan(self.a, 'impacto', 0.0)
        self.assertFalse(any(x['type'] == 'zoom' for x in zero['actions']))

    def test_variants_really_differ(self):
        dyn = direction.describe(direction.build_plan(self.a, 'impacto', 0.8))
        cine = direction.describe(direction.build_plan(self.a, 'cinematografico', 0.8))
        self.assertGreater(dyn['seconds_removed'], cine['seconds_removed'])
        self.assertGreater(dyn['zooms'], 0)
        self.assertTrue(cine['color'])
        self.assertNotEqual(dyn['subtitle_style'], cine['subtitle_style'])

    def test_keep_intervals_and_remap(self):
        plan = {'actions': [{'type': 'cut_pause', 'start': 3.0, 'end': 4.0, 'enabled': True},
                            {'type': 'cut_pause', 'start': 6.0, 'end': 6.5, 'enabled': False},
                            {'type': 'zoom', 'start': 5.0, 'end': 6.0, 'params': {'amount': 0.5}, 'enabled': True}]}
        keep = direction.keep_intervals(plan, 10)
        self.assertEqual(keep, [(0.0, 3.0), (4.0, 10.0)])
        expr = direction.zoom_expression(plan, lambda t: media.remap_time(t, keep))
        self.assertIn('0.200*', expr)        # limitado a ZOOM_MAX
        self.assertIn('it-4.000', expr)      # 5.0 s no original → 4.0 s depois do corte


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class RenderWithPlanTests(unittest.TestCase):
    def test_zoom_color_and_fades_render(self):
        tmp = Path(tempfile.mkdtemp(prefix='render-plano-'))
        src = make_video(tmp / 'fonte.mp4', size='1280x720', duration=6, fps=25)
        info = media.probe_media(src)
        plan = {'settings': {'color': {'contrast': 1.07, 'saturation': 0.9, 'vignette': True}, 'video_fade': 0.4,
                             'audio_fade': 0.05, 'denoise': True},
                'actions': [{'type': 'zoom', 'start': 1.0, 'end': 3.0, 'params': {'amount': 0.12}, 'enabled': True}]}
        post = direction.video_post_filters(plan, (1080, 1920), 25, 6.0, lambda t: t)
        audio = direction.audio_filters(plan, 6.0)
        result = media.export_clip(src, info, tmp / 'out', 'teste', 0, 6, fmt='9:16', framing='blur',
                                   post=post, audio_extra=audio)
        self.assertTrue(result['validation']['valid'], result['validation'])
        out = tmp / 'out' / result['file']
        self.assertAlmostEqual(media.probe_media(out)['duration'], 6, delta=0.3)


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class PlanServiceTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'conversa.mp4', size='1280x720', duration=30, fps=25)

    def test_variants_edit_and_export_with_plan(self):
        res = self.create(self.video)
        pid = res['id']
        clip = service.get_project(pid)['clips'][0]
        view = service.create_edit_plans(pid, clip['id'], {'variants': True, 'intensity': 0.7})
        self.assertEqual(set(view['plans']), {'dinamico', 'equilibrado', 'cinematografico'})
        self.assertIn(view['selected'], view['plans'])
        self.assertFalse(view['stale'])
        # Cache: a segunda análise não relê o áudio.
        cache = service._plan_cache(pid, clip)
        mtime = cache.stat().st_mtime
        service.create_edit_plans(pid, clip['id'], {'style': 'podcast'})
        self.assertEqual(cache.stat().st_mtime, mtime)

        view = service.create_edit_plans(pid, clip['id'], {'variants': True})
        plan = view['plans']['cinematografico']
        if plan['actions']:
            first = str(plan['actions'][0]['id'])
            view = service.update_edit_plan(pid, clip['id'], 'cinematografico', {'actions': {first: False},
                                                                                   'selected': True})
            self.assertFalse(view['plans']['cinematografico']['actions'][0]['enabled'])
        self.assertEqual(view['selected'], 'cinematografico')

        job = service.request_export(pid, clip['id'], {'format': '9:16', 'subtitles': True,
                                                       'edit_plan': 'cinematografico'})
        done = service.manager.wait(job['id'], 240)
        self.assertEqual(done['status'], 'done', done.get('error'))
        record = service.get_project(pid)['clips'][0]['exports']['9:16']
        self.assertEqual(record['edit_plan']['style'], 'cinematografico')
        self.assertTrue(record['validation']['valid'])

        service.edit_clip(pid, clip['id'], clip['start'] + 1, clip['end'], clip['title'])
        with self.assertRaises(AppError) as ctx:
            service.request_export(pid, clip['id'], {'format': '9:16', 'edit_plan': 'cinematografico'})
        self.assertEqual(ctx.exception.code, 'plan_stale')


if __name__ == '__main__':
    unittest.main()
