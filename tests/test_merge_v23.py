"""v2.3: seguir rosto, legendas dinâmicas + gancho e curadoria com IA (provedores simulados, sem rede)."""
import json
import os
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import config, curation, media, reframe, service, settings, storage, subtitles, transcription, watcher
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, fake_transcript, make_video


def eval_ffmpeg_expr(expr: str, t: float) -> float:
    """Avalia a expressão de tempo gerada para o crop com as mesmas funções do FFmpeg."""
    env = {'t': t, 'gte': lambda a, b: 1.0 if a >= b else 0.0, 'lt': lambda a, b: 1.0 if a < b else 0.0,
           'between': lambda x, a, b: 1.0 if a <= x <= b else 0.0, 'min': min}
    return eval(expr, {'__builtins__': {}}, env)  # noqa: S307 - expressão gerada pelo próprio teste


# --------------------------------------------------------------------------- seguir rosto

class CameraTests(unittest.TestCase):
    def test_camera_respects_dead_zone_and_borders(self):
        # alvo parado: a câmera não treme
        path = reframe.camera_path([(i / 5, 0.5) for i in range(20)], crop_frac=0.3)
        self.assertTrue(all(abs(x - 0.5) < 1e-9 for _, x in path))
        # alvo colado na borda: o recorte nunca sai do quadro
        path = reframe.camera_path([(i / 5, 0.99) for i in range(40)], crop_frac=0.3)
        self.assertTrue(all(0.15 <= x <= 0.85 for _, x in path))
        # alvo que anda menos que a largura do recorte: a câmera se aproxima aos poucos (suave)
        path = reframe.camera_path([(0, 0.4)] + [(i / 5, 0.6) for i in range(1, 30)], crop_frac=0.3)
        steps = [abs(b[1] - a[1]) for a, b in zip(path, path[1:])]
        self.assertLess(max(steps), 0.1)
        self.assertGreater(path[-1][1], 0.55)
        # salto maior que o recorte (outra pessoa): corte seco, sem deslizar pelo cenário
        path = reframe.camera_path([(0, 0.3)] + [(i / 5, 0.7) for i in range(1, 30)], crop_frac=0.3)
        self.assertEqual(path[1], (0.199, 0.3))       # posição antiga até o instante da troca
        self.assertAlmostEqual(path[2][1], 0.7, delta=0.01)

    def test_gaps_hold_last_position(self):
        path = reframe.camera_path([(0, 0.4), (0.2, None), (0.4, None)], crop_frac=0.3)
        self.assertEqual(len(path), 3)
        self.assertAlmostEqual(path[-1][1], 0.4, places=3)

    def test_simplify_keeps_shape_with_few_points(self):
        pts = [(i / 10, 0.3 + 0.004 * i) for i in range(101)]
        simple = reframe.simplify(pts)
        self.assertLessEqual(len(simple), 3)
        self.assertEqual(simple[0], pts[0])
        self.assertEqual(simple[-1], pts[-1])

    def test_crop_expression_is_piecewise_linear_and_centered_outside_faces(self):
        shots = [{'start': 0.0, 'end': 5.0, 'layout': 'fill', 'points': [(0.0, 0.3), (2.0, 0.5), (5.0, 0.5)]},
                 {'start': 5.0, 'end': 8.0, 'layout': 'fit', 'points': []},
                 {'start': 8.0, 'end': 10.0, 'layout': 'fill', 'points': [(8.0, 0.7)]}]
        expr = reframe.crop_x_expression(shots)
        self.assertAlmostEqual(eval_ffmpeg_expr(expr, 0.0), 0.3, places=4)
        self.assertAlmostEqual(eval_ffmpeg_expr(expr, 1.0), 0.4, places=4)
        self.assertAlmostEqual(eval_ffmpeg_expr(expr, 3.0), 0.5, places=4)
        self.assertAlmostEqual(eval_ffmpeg_expr(expr, 6.0), 0.5, places=4)   # cena sem rosto: centro
        self.assertAlmostEqual(eval_ffmpeg_expr(expr, 9.0), 0.7, places=4)
        enable = reframe.fit_enable_expression(shots)
        self.assertEqual(eval_ffmpeg_expr(enable, 6.0), 1.0)
        self.assertEqual(eval_ffmpeg_expr(enable, 2.0), 0.0)
        self.assertIsNone(reframe.fit_enable_expression(shots[:1]))

    def test_applicable_only_when_output_is_narrower(self):
        self.assertTrue(reframe.applicable({'width': 1920, 'height': 1080}, (1080, 1920)))
        self.assertTrue(reframe.applicable({'width': 1920, 'height': 1080}, (1080, 1080)))
        self.assertFalse(reframe.applicable({'width': 1080, 'height': 1920}, (1080, 1920)))
        self.assertFalse(reframe.applicable({'width': 1920, 'height': 1080}, (1920, 1080)))


@unittest.skipUnless(HAS_FFMPEG and reframe.available(), 'FFmpeg e OpenCV necessários')
class TrackExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='autocortes-track-'))
        cls.video = make_video(cls.tmp / 'entrevista.mp4', size='1280x720', duration=10)
        cls.info = media.probe_media(cls.video)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fake_faces(self, gray):
        return [(0.62, 0.3, 0.12, 0.22)]   # rosto fixo à direita

    def test_plan_follows_face_and_marks_scene_without_face(self):
        calls = {'n': 0}

        def faces(gray):  # rosto só nos primeiros quadros (até ~5 s a 5 amostras/s)
            calls['n'] += 1
            return [(0.62, 0.3, 0.12, 0.22)] if calls['n'] <= 25 else []

        with mock.patch.object(reframe, 'detect_faces', side_effect=faces), \
                mock.patch.object(reframe, 'yunet_available', return_value=False):
            plan = reframe.plan(self.video, self.info, 0, 10, (1080, 1920), scenes=[5.0])
        self.assertTrue(plan['ok'], plan['message'])
        layouts = [(s['layout'], round(s['start'])) for s in plan['shots']]
        self.assertEqual(layouts, [('fill', 0), ('fit', 5)])
        fill = plan['shots'][0]
        self.assertAlmostEqual(fill['points'][-1][1], 0.68, delta=0.05)   # centro do rosto
        json.dumps(plan)  # serializável (sem tipos do numpy)

    def test_no_face_falls_back(self):
        with mock.patch.object(reframe, 'detect_faces', return_value=[]), \
                mock.patch.object(reframe, 'yunet_available', return_value=False):
            plan = reframe.plan(self.video, self.info, 0, 6, (1080, 1920))
        self.assertFalse(plan['ok'])
        self.assertIn('fundo desfocado', plan['message'])

    def test_track_export_is_valid(self):
        with mock.patch.object(reframe, 'detect_faces', side_effect=self.fake_faces), \
                mock.patch.object(reframe, 'yunet_available', return_value=False):
            plan = reframe.plan(self.video, self.info, 1, 7, (1080, 1920), scenes=[4.0])
        result = media.export_clip(self.video, self.info, self.tmp / 'out', 'clip', 1, 7, fmt='9:16',
                                   framing='track', track=plan)
        self.assertTrue(result['validation']['valid'])
        self.assertTrue(result['face_tracking'])
        self.assertEqual((result['width'], result['height']), (1080, 1920))

    def test_track_without_plan_exports_as_blur(self):
        result = media.export_clip(self.video, self.info, self.tmp / 'out2', 'clip', 0, 4, fmt='1:1',
                                   framing='track', track={'ok': False, 'shots': []})
        self.assertTrue(result['validation']['valid'])
        self.assertFalse(result['face_tracking'])


# --------------------------------------------------------------------------- legendas

class DynamicSubtitleTests(unittest.TestCase):
    def setUp(self):
        self.transcript = fake_transcript(12)

    def test_word_lines_are_short_and_relative(self):
        lines = subtitles.build_word_lines(self.transcript, 3.0, 9.0)
        self.assertTrue(lines)
        self.assertTrue(all(1 <= len(line) <= subtitles.WORDS_PER_LINE for line in lines))
        self.assertGreaterEqual(lines[0][0]['start'], 0)
        self.assertLessEqual(lines[-1][-1]['end'], 6.0)

    def test_dynamic_ass_highlights_each_word_and_has_hook(self):
        lines = subtitles.build_word_lines(self.transcript, 0, 6)
        ass = subtitles.to_ass_dynamic(lines, 1080, 1920, hook_text='O {erro} de todo dia', clip_len=6)
        self.assertIn('PlayResY: 1920', ass)
        words = sum(len(line) for line in lines)
        self.assertEqual(ass.count(subtitles.HIGHLIGHT), words)
        self.assertIn(',Hook,', ass)
        self.assertIn('O (erro) de todo dia', ass)  # chaves não viram comandos do libass
        self.assertNotIn('{erro}', ass)

    def test_classic_ass_with_hook_and_hook_only(self):
        ass = subtitles.to_ass([{'start': 0, 'end': 2, 'text': 'olá'}], 1080, 1920, hook_text='Gancho', clip_len=2)
        self.assertRegex(ass, r'Dialogue: 1,0:00:00\.00,0:00:02\.00,Hook')
        only = subtitles.to_ass([], 1080, 1920, hook_text='Só o gancho')
        self.assertIn('Só o gancho', only)
        self.assertNotIn(',Default,,', only.split('[Events]')[1])

    def test_no_word_timestamps_gives_no_lines(self):
        plain = [{'start': 0, 'end': 3, 'text': 'sem palavras'}]
        self.assertEqual(subtitles.build_word_lines(plain, 0, 3), [])

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg necessário')
    def test_dynamic_subtitles_burn_into_valid_mp4(self):
        tmp = Path(tempfile.mkdtemp(prefix='autocortes-dyn-'))
        try:
            video = make_video(tmp / 'v.mp4', size='1280x720', duration=8)
            info = media.probe_media(video)
            lines = subtitles.build_word_lines(self.transcript, 1, 7)
            ass = subtitles.to_ass_dynamic(lines, 1080, 1920, hook_text='Gancho forte', clip_len=6)
            result = media.export_clip(video, info, tmp / 'out', 'clip', 1, 7, fmt='9:16', framing='blur',
                                       ass_text=ass, srt_text='1\n00:00:00,000 --> 00:00:01,000\nolá\n')
            self.assertTrue(result['validation']['valid'])
            self.assertTrue(result['subtitles_burned'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- curadoria

PHRASES = [
    'Você sabia que a maioria das pessoas erra o preço do café todos os dias?',
    'O segredo está em comprar grãos frescos direto do produtor de Minas.',
    'Primeiro, compare o preço do quilo antes de escolher a marca.',
    'Depois, guarde o café em pote fechado longe da luz e do calor.',
    'Agora vamos falar sobre a seca que afetou as plantações neste ano.',
    'A colheita caiu quase um terço e o preço subiu nas prateleiras.',
]


def gemini_reply(obj):
    return {'candidates': [{'finishReason': 'STOP',
                            'content': {'parts': [{'text': json.dumps(obj, ensure_ascii=False)}]}}]}


class FakeProvider:
    """Simula proposta e juiz. Primeira proposta tem título fiel; a segunda, título inventado."""

    def __init__(self, wrap, fail=False):
        self.wrap, self.fail, self.calls = wrap, fail, []

    def __call__(self, url, payload, headers, provider):
        self.calls.append((url, payload, headers))
        if self.fail:
            raise AppError('Serviço indisponível.', 502, 'curation_http')
        text = json.dumps(payload)
        if 'cortes_propostos' in text or 'Proponha' in text:
            return self.wrap({'clips': [
                {'start': 0, 'end': 3, 'title': 'O erro no preço do café', 'hook_text': 'Você erra o preço do café?'},
                {'start': 4, 'end': 5, 'title': 'Bilionário revela fortuna secreta', 'hook_text': 'Fortuna secreta revelada'},
                {'start': 99, 'end': 120, 'title': 'fora do texto', 'hook_text': ''},
            ]})
        ids = [int(m) for m in re.findall(r'id=(\d+)', payload['contents'][0]['parts'][0]['text'])]
        return self.wrap({'results': [
            {'id': i, 'gancho': 9 - i, 'coerencia': 8, 'valor': 7.5, 'tendencia': 6} for i in ids]})


class CurationTests(unittest.TestCase):
    def setUp(self):
        self.transcript = fake_transcript(25, PHRASES)

    def run_curation(self, provider, wrap):
        fake = FakeProvider(wrap)
        env = {'GEMINI_API_KEY': 'AIza-teste'}
        with mock.patch.dict(os.environ, env), mock.patch.object(curation, '_post', fake):
            clips, status = curation.curate(provider, self.transcript, 25, 12, 3, [])
        return clips, status, fake

    def test_gemini_curation_scores_and_grounds_titles(self):
        clips, status, fake = self.run_curation('gemini', gemini_reply)
        self.assertEqual(status['state'], 'ok')
        self.assertEqual(len(clips), 2)
        best = clips[0]
        self.assertEqual(best['rank'], 1)
        self.assertEqual(best['ai_curation'], 'gemini')
        self.assertEqual(best['title'], 'O erro no preço do café')
        self.assertEqual(best['hook_text'], 'Você erra o preço do café?')
        self.assertNotIn('hashtags', best)          # pedido enxuto: sem hashtags, resumo nem comentários
        self.assertNotIn('ai_summary', best)
        self.assertTrue(any(r.startswith('Gancho 9.0/10') for r in best['reasons']))
        self.assertGreaterEqual(best['score'], clips[1]['score'])
        # título e gancho inventados são descartados
        other = clips[1]
        self.assertEqual(other['title_ai_rejected'], 'Bilionário revela fortuna secreta')
        self.assertNotIn('hook_text', other)
        self.assertNotIn('Bilionário', other['title'])
        # sem sobreposição e dentro do vídeo
        self.assertLessEqual(clips[0]['end'], 25)
        a, b = sorted(clips, key=lambda c: c['start'])
        self.assertLessEqual(a['end'], b['start'] + 0.2 * (b['end'] - b['start']))
        # pedido com esquema de resposta JSON, chave no cabeçalho (não na URL) e transcrição como não confiável
        url, payload, headers = fake.calls[0]
        self.assertTrue(url.endswith(':generateContent'))
        self.assertNotIn('AIza', url)
        self.assertEqual(headers['x-goog-api-key'], 'AIza-teste')
        config_ = payload['generationConfig']
        self.assertEqual((config_['responseMimeType'], config_['temperature']), ('application/json', 0))
        self.assertEqual(config_['responseSchema']['type'], 'OBJECT')
        self.assertNotIn('additionalProperties', json.dumps(config_['responseSchema']))
        self.assertIn('NÃO CONFIÁVEL', payload['systemInstruction']['parts'][0]['text'])
        self.assertIn('<transcricao>', payload['contents'][0]['parts'][0]['text'])

    def test_missing_key_and_bad_response_raise(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GOOGLE_API_KEY': ''}):
            with self.assertRaises(AppError) as ctx:
                curation.curate('gemini', self.transcript, 25, 12, 3, [])
        self.assertEqual(ctx.exception.code, 'curation_not_configured')
        for bad in ({'candidates': [{'content': {'parts': [{'text': 'sem json'}]}}]},
                    {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'text': '{'}]}}]},
                    {'promptFeedback': {'blockReason': 'SAFETY'}}):
            with self.subTest(bad=bad), mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'x'}), \
                    mock.patch.object(curation, '_post', lambda *a, **k: bad):
                with self.assertRaises(AppError) as ctx:   # todas as respostas ruins: nenhum trecho proposto
                    curation.curate('gemini', self.transcript, 25, 12, 3, [])
                self.assertEqual(ctx.exception.code, 'curation_empty')

    def test_judge_scores_are_clamped(self):
        self.assertEqual(curation._score(15), 10.0)
        self.assertEqual(curation._score(-3), 0.0)
        self.assertEqual(curation._score('x'), 0.0)
        self.assertEqual(curation._score(float('nan')), 0.0)

class CurationSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='autocortes-cur-'))
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp / 'data'

    def tearDown(self):
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_send_nothing(self):
        s = settings.load()
        self.assertEqual(s['curation']['provider'], 'ollama')      # local: nada sai do computador
        self.assertFalse(s['curation']['consent'])
        self.assertEqual(s['autopilot']['subtitle_style'], 'dinamica')

    def test_cloud_provider_requires_consent_and_records_time(self):
        with self.assertRaises(AppError) as ctx:
            settings.update({'curation': {'provider': 'gemini', 'consent': False}})
        self.assertEqual(ctx.exception.code, 'consent_required')
        saved = settings.update({'curation': {'provider': 'gemini', 'consent': True, 'prompt': 'café'}})
        self.assertIsNotNone(saved['curation']['consent_at'])
        saved = settings.update({'curation': {'provider': 'heuristica', 'consent': False}})
        self.assertIsNone(saved['curation']['consent_at'])
        with self.assertRaises(AppError):
            settings.update({'curation': {'provider': 'outro', 'consent': True}})

    def test_retired_cloud_providers_become_ollama(self):
        (config.DATA_DIR).mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / 'settings.json').write_text(json.dumps(
            {'curation': {'provider': 'openai', 'consent': True}, 'autopilot': {'max_clips': 7}}), encoding='utf-8')
        s = settings.load()
        self.assertEqual((s['curation']['provider'], s['curation']['consent']), ('ollama', False))
        self.assertEqual(s['autopilot']['max_clips'], 7)            # o resto das configurações continua

    def test_autopilot_accepts_track_and_rejects_bad_style(self):
        saved = settings.update({'autopilot': {'framing': 'track', 'subtitle_style': 'classica', 'hook': False}})
        self.assertEqual(saved['autopilot']['framing'], 'track')
        with self.assertRaises(AppError):
            settings.update({'autopilot': {'subtitle_style': 'neon'}})


# --------------------------------------------------------------------------- ponta a ponta pela fila

OWNED = {'source_rights': 'owned', 'external_audio': 'no', 'graphics_rights': 'none',
         'third_party_footage': 'no', 'monetization': 'yes'}


def wait_until(predicate, timeout=240, step=0.2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


@unittest.skipUnless(HAS_FFMPEG and reframe.available(), 'FFmpeg e OpenCV necessários')
class PipelineV23Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='autocortes-v23-'))
        cls.saved = (config.DATA_DIR, config.WATCH_DIR, config.OUTPUT_DIR, transcription.available,
                     transcription.transcribe)
        config.DATA_DIR = cls.tmp / 'data'
        config.WATCH_DIR = cls.tmp / 'entrada'
        config.OUTPUT_DIR = cls.tmp / 'saida'
        transcription.available = lambda: True
        transcription.transcribe = lambda wav, dur, on_progress=None, cancel_event=None: (
            fake_transcript(dur, PHRASES), {'state': 'ok', 'message': 'simulada'})
        cls.video = make_video(cls.tmp / 'Minha entrevista.mp4', size='1280x720', duration=26)
        service.startup()
        watcher.stop()

    @classmethod
    def tearDownClass(cls):
        service.shutdown()
        (config.DATA_DIR, config.WATCH_DIR, config.OUTPUT_DIR, transcription.available,
         transcription.transcribe) = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def analyze(self, fake):
        form = {**OWNED, 'target_seconds': 12, 'desired_count': 3, 'autopilot': False}
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'AIza-teste'}), mock.patch.object(curation, '_post', fake):
            with self.video.open('rb') as fh:
                pid = service.create_project(iter(lambda: fh.read(1 << 20), b''), self.video.name, form)['id']
            p = wait_until(lambda: (lambda q: q if q.get('status') in ('ready', 'error') else None)(
                storage.read_project(pid)))
        self.assertEqual(p['status'], 'ready', p.get('error'))
        return pid, p

    def test_cloud_curation_then_tracked_dynamic_export(self):
        settings.update({'curation': {'provider': 'gemini', 'consent': True}})
        try:
            pid, p = self.analyze(FakeProvider(gemini_reply))
        finally:
            settings.update({'curation': {'provider': 'heuristica', 'consent': False}})
        self.assertEqual(p['curation']['state'], 'ok', p['curation'])
        self.assertTrue(all(c.get('ai_curation') == 'gemini' for c in p['clips']))
        self.assertEqual(p['cloud_consents'][-1]['provider'], 'Google Gemini (nuvem)')
        clip = p['clips'][0]
        with mock.patch.object(reframe, 'detect_faces', return_value=[(0.6, 0.3, 0.12, 0.22)]), \
                mock.patch.object(reframe, 'yunet_available', return_value=False):
            job = service.request_export(pid, clip['id'], {'format': '9:16', 'framing': 'track', 'subtitles': True,
                                                           'subtitle_style': 'dinamica', 'hook': True})
            done = wait_until(lambda: (lambda j: j if j['status'] in ('done', 'error', 'cancelled') else None)(
                service.get_job(job['id'])))
        self.assertEqual(done['status'], 'done', done)
        record = next(c for c in storage.read_project(pid)['clips'] if c['id'] == clip['id'])['exports']['9:16']
        self.assertTrue(record['validation']['valid'])
        self.assertTrue(record['face_tracking'])
        self.assertTrue(record['subtitles_burned'])
        package = (storage.project_dir(pid) / 'clips' / record['package_file']).read_text(encoding='utf-8')
        self.assertIn('TÍTULO SUGERIDO', package)

    def test_cloud_failure_keeps_heuristic_selection(self):
        settings.update({'curation': {'provider': 'gemini', 'consent': True}})
        try:
            pid, p = self.analyze(FakeProvider(gemini_reply, fail=True))
        finally:
            settings.update({'curation': {'provider': 'heuristica', 'consent': False}})
        self.assertEqual(p['curation']['state'], 'failed')
        self.assertIn('mantida a seleção heurística', p['curation']['message'])
        self.assertTrue(p['clips'])
        self.assertTrue(all('ai_curation' not in c for c in p['clips']))


if __name__ == '__main__':
    unittest.main()
