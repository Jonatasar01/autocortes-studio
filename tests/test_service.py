"""Testes de integração da camada de serviço (fila, persistência, concorrência, segurança).

Não dependem do FastAPI: exercitam as mesmas funções chamadas pela API, com FFmpeg real.
"""
import asyncio
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app import config, service, storage, transcription
from app.errors import AppError
from app.security import LocalSecurityMiddleware
from tests.helpers import HAS_FFMPEG, fake_transcript, make_video

OWNED = {'source_rights': 'owned', 'external_audio': 'no', 'graphics_rights': 'none',
         'third_party_footage': 'no', 'monetization': 'no', 'target_seconds': '10', 'desired_count': '3'}


def chunks(path: Path, size: int = 1 << 20):
    with path.open('rb') as fh:
        while data := fh.read(size):
            yield data


class ServiceBase(unittest.TestCase):
    fake_whisper = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='autocortes-svc-'))
        cls.old_data = config.DATA_DIR
        config.DATA_DIR = cls.tmp / 'data'
        cls.old_available, cls.old_transcribe = transcription.available, transcription.transcribe
        if cls.fake_whisper:
            transcription.available = lambda: True
            transcription.transcribe = lambda wav, dur, on_progress=None, cancel_event=None: (
                fake_transcript(dur), {'state': 'ok', 'message': 'Transcrição simulada (teste).'})
        service.startup()

    @classmethod
    def tearDownClass(cls):
        service.shutdown()
        transcription.available, transcription.transcribe = cls.old_available, cls.old_transcribe
        config.DATA_DIR = cls.old_data
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def create(self, path: Path, form: dict | None = None, wait: bool = True) -> dict:
        res = service.create_project(chunks(path), path.name, form or OWNED)
        if wait:
            job = service.manager.wait(res['job']['id'], 180)
            self.assertEqual(job['status'], 'done', job.get('error'))
        return res


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class FlowTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'Entrevista café & ação.mp4', size='1280x720', duration=40, fps=25)

    def test_full_flow_upload_analyze_edit_export_report(self):
        res = self.create(self.video)
        p = service.get_project(res['id'])
        self.assertEqual(p['status'], 'ready')
        self.assertNotIn('filename', p)
        self.assertEqual(p['selection']['mode'], 'transcricao')
        self.assertGreaterEqual(len(p['clips']), 2)
        self.assertEqual(p['rights']['level'], 'informado_sem_alertas')
        self.assertEqual(p['music_review']['status'], 'not_requested')
        clip = service.edit_clip(res['id'], 1, 1.0, 7.5, 'Título <editado>\x00')
        self.assertEqual(clip['title'], 'Título <editado>')
        job = service.request_export(res['id'], 1, {'format': '9:16', 'framing': 'blur', 'subtitles': True})
        dup = service.request_export(res['id'], 1, {'format': '9:16', 'framing': 'blur', 'subtitles': True})
        self.assertEqual(job['id'], dup['id'])
        self.assertTrue(dup['duplicate'])
        done = service.manager.wait(job['id'], 180)
        self.assertEqual(done['status'], 'done', done.get('error'))
        self.assertTrue(done['result']['validation']['valid'])
        self.assertTrue(done['result']['subtitles_burned'])
        path, name = service.export_path(res['id'], 1, '9:16')
        self.assertTrue(path.is_file())
        self.assertTrue(name.endswith('_9x16.mp4'))
        self.assertTrue(service.export_path(res['id'], 1, '9:16', 'srt')[0].is_file())
        report = service.report(res['id'])
        self.assertTrue(report['clips'][0]['exports']['9:16']['validado'])
        json.dumps(report)  # serializável

    def test_project_jobs_lists_exports_for_the_queue_panel(self):
        res = self.create(self.video)
        job = service.request_export(res['id'], 1, {'format': '16:9', 'subtitles': False})
        service.manager.wait(job['id'], 180)
        jobs = service.project_jobs(res['id'])
        self.assertEqual([j['kind'] for j in jobs], ['export'])  # a análise não entra no painel
        self.assertEqual((jobs[0]['clip_id'], jobs[0]['format'], jobs[0]['preview']), (1, '16:9', False))
        with self.assertRaises(AppError):
            service.project_jobs('0' * 32)

    def test_color_filter_samples_and_export(self):
        from app import filters
        res = self.create(self.video)
        import subprocess
        strip = filters.sample_strip(res['id'], 5.0)
        size = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                               str(strip)], capture_output=True, text=True, check=True).stdout.strip()
        width, height = (int(v) for v in size.split(','))
        self.assertEqual(height, filters.SAMPLE_H)
        self.assertGreater(width, filters.SAMPLE_H * len(filters.FILTERS))   # um quadro por filtro
        self.assertEqual(filters.sample_strip(res['id'], 5.0), strip)                  # cache
        job = service.request_export(res['id'], 1, {'format': '9:16', 'subtitles': False, 'filter': 'pb'})
        self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        self.assertEqual(service.manager.get(job['id'])['params']['filter'], 'pb')
        with self.assertRaises(AppError) as ctx:
            service.request_export(res['id'], 1, {'format': '9:16', 'filter': 'inexistente'})
        self.assertEqual(ctx.exception.code, 'bad_filter')

    def test_reanalyze_ready_project_and_keep_clips_on_failure(self):
        res = self.create(self.video)
        again = service.reanalyze(res['id'])
        self.assertEqual(service.manager.wait(again['id'], 180)['status'], 'done')
        self.assertEqual(storage.read_project(res['id'])['status'], 'ready')
        original = transcription.transcribe

        def broken(*a, **k):
            raise RuntimeError('falha simulada')
        transcription.transcribe = broken
        try:
            job = service.reanalyze(res['id'])
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'failed')
        finally:
            transcription.transcribe = original
        p = storage.read_project(res['id'])
        self.assertEqual(p['status'], 'ready')     # os cortes anteriores continuam valendo
        self.assertTrue(p['clips'])

    def test_analysis_cut_by_a_restart_resumes_by_itself(self):
        import sqlite3
        res = self.create(self.video)
        job_id = storage.read_project(res['id'])['analysis_job']
        service.shutdown()
        try:
            with sqlite3.connect(config.DATA_DIR / 'autocortes.db') as db:   # como se o app caísse no meio
                db.execute("UPDATE jobs SET status='running', finished=NULL WHERE id=?", (job_id,))
            with storage.edit_project(res['id']) as p:
                p['status'] = 'analyzing'
        finally:
            service.startup()
        p = storage.read_project(res['id'])
        self.assertNotEqual(p['analysis_job'], job_id)
        self.assertEqual(service.manager.wait(p['analysis_job'], 180)['status'], 'done')
        self.assertEqual(storage.read_project(res['id'])['status'], 'ready')

    def test_reselect_reuses_the_saved_transcript(self):
        res = self.create(self.video)
        original = transcription.transcribe

        def must_not_run(*a, **k):
            raise AssertionError('refazer a seleção não pode transcrever de novo')
        transcription.transcribe = must_not_run
        try:
            job = service.reselect(res['id'])
            done = service.manager.wait(job['id'], 180)
        finally:
            transcription.transcribe = original
        self.assertEqual(done['status'], 'done', done.get('error'))
        p = storage.read_project(res['id'])
        self.assertEqual(p['status'], 'ready')
        self.assertTrue(p['clips'])
        self.assertEqual(p['curation_profile']['provider'], 'heuristica')

    def test_reselect_requires_a_ready_project_with_transcript(self):
        res = self.create(self.video)
        with storage.edit_project(res['id']) as p:
            p['transcription'] = {'state': 'failed', 'message': 'x'}
        with self.assertRaises(AppError) as ctx:
            service.reselect(res['id'])
        self.assertEqual(ctx.exception.code, 'no_transcript')

    def test_download_clip_with_three_digit_number(self):
        # Lives longas passam de 99 cortes: o arquivo do corte 164 é clip_164_9x16_v1.mp4.
        res = self.create(self.video)
        clips_dir = storage.project_dir(res['id']) / 'clips'
        clips_dir.mkdir(exist_ok=True)
        (clips_dir / 'clip_164_9x16_v1.mp4').write_bytes(b'x')
        with storage.edit_project(res['id']) as p:
            p['clips'][0]['exports'] = {'9:16': {'file': 'clip_164_9x16_v1.mp4', 'format': '9:16'}}
        path, _ = service.export_path(res['id'], p['clips'][0]['id'], '9:16')
        self.assertEqual(path.name, 'clip_164_9x16_v1.mp4')

    def test_reexport_replaces_previous_version(self):
        res = self.create(self.video)
        first = service.manager.wait(service.request_export(res['id'], 1, {'format': '16:9', 'subtitles': False})['id'], 180)
        service.edit_clip(res['id'], 1, 0.5, 6, 'Outro')
        second = service.manager.wait(service.request_export(res['id'], 1, {'format': '16:9', 'subtitles': False})['id'], 180)
        clips_dir = storage.project_dir(res['id']) / 'clips'
        self.assertFalse((clips_dir / first['result']['file']).exists())
        self.assertTrue((clips_dir / second['result']['file']).exists())

    def test_concurrent_edit_during_export_is_not_lost(self):
        res = self.create(self.video)
        job = service.request_export(res['id'], 1, {'format': '1:1', 'subtitles': False})
        p = storage.read_project(res['id'])
        other = p['clips'][1]
        service.edit_clip(res['id'], other['id'], other['start'], other['end'], 'Editado durante exportação')
        service.manager.wait(job['id'], 180)
        after = storage.read_project(res['id'])
        self.assertEqual(next(c for c in after['clips'] if c['id'] == other['id'])['title'], 'Editado durante exportação')
        self.assertIn('1:1', after['clips'][0]['exports'])

    def test_rights_acknowledgement_required_for_high_risk(self):
        res = self.create(self.video, {**OWNED, 'source_rights': 'third_party'})
        with self.assertRaises(AppError) as ctx:
            service.request_export(res['id'], 1, {'format': '16:9'})
        self.assertEqual(ctx.exception.code, 'rights_ack_required')
        job = service.request_export(res['id'], 1, {'format': '16:9', 'subtitles': False, 'acknowledge_rights': True})
        service.manager.wait(job['id'], 180)
        self.assertTrue(service.report(res['id'])['rights_acknowledgements'])

    def test_cancel_analysis_and_reanalyze(self):
        original = transcription.transcribe

        def slow(wav, dur, on_progress=None, cancel_event=None):
            for _ in range(100):
                if cancel_event.is_set():
                    from app.errors import Cancelled
                    raise Cancelled()
                time.sleep(0.05)
            return [], {'state': 'no_speech', 'message': 'x'}
        transcription.transcribe = slow
        try:
            res = self.create(self.video, wait=False)
            for _ in range(100):
                if service.get_job(res['job']['id'])['stage'] == 'transcrevendo':
                    break
                time.sleep(0.1)
            service.cancel_job(res['job']['id'])
            job = service.manager.wait(res['job']['id'], 60)
        finally:
            transcription.transcribe = original
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(storage.read_project(res['id'])['status'], 'cancelled')
        again = service.reanalyze(res['id'])
        self.assertEqual(service.manager.wait(again['id'], 180)['status'], 'done')
        self.assertEqual(storage.read_project(res['id'])['status'], 'ready')

    def test_scene_detection_failure_keeps_the_analysis(self):
        # Em vídeos longos a falha das cenas só aparecia depois de horas de transcrição, e descartava tudo.
        from app import media
        original = media.detect_scenes

        def broken(*a, **k):
            raise PermissionError(32, 'O arquivo já está sendo usado por outro processo')
        media.detect_scenes = broken
        try:
            res = self.create(self.video)
        finally:
            media.detect_scenes = original
        p = storage.read_project(res['id'])
        self.assertEqual(p['status'], 'ready')
        self.assertEqual(p['selection']['mode'], 'transcricao')
        self.assertTrue(p['clips'])

    def test_ai_curation_is_merged_with_the_heuristic(self):
        # A curadoria trouxe um corte; os da heurística que não se sobrepõem a ele continuam na lista.
        from unittest import mock
        from app import curation, settings
        ai_clip = {'start': 0.5, 'end': 9.0, 'score': 88.0, 'title': 'Escolhido pela IA', 'description': 'x',
                   'reasons': ['IA'], 'selection_mode': 'transcricao', 'ai_curation': 'anthropic'}
        settings.update({'curation': {'provider': 'anthropic', 'consent': True}})
        try:
            with mock.patch.object(curation, 'curate', return_value=([ai_clip], {'state': 'ok', 'provider': 'anthropic',
                                                                               'message': 'ok'})):
                res = self.create(self.video)
        finally:
            settings.update({'curation': {'provider': 'heuristica', 'consent': False}})
        p = storage.read_project(res['id'])
        self.assertEqual(p['curation']['state'], 'ok')
        sources = [c['sources'] for c in p['clips']]
        self.assertTrue(any('ia' in s for s in sources), sources)
        self.assertTrue(any(s == ['heuristica'] for s in sources), sources)   # a heurística não sumiu
        self.assertEqual([c['rank'] for c in p['clips']], list(range(1, len(p['clips']) + 1)))

    def test_transcription_failure_falls_back_honestly(self):
        original = transcription.transcribe
        transcription.transcribe = lambda *a, **k: ([], {'state': 'failed', 'message': 'Falha simulada na transcrição.'})
        try:
            res = self.create(self.video)
        finally:
            transcription.transcribe = original
        p = storage.read_project(res['id'])
        self.assertEqual(p['selection']['mode'], 'temporal')
        self.assertTrue(all(c['score'] is None for c in p['clips']))
        self.assertIn('Falha simulada', p['clips'][0]['description'])

    def test_video_without_audio(self):
        mute = make_video(self.tmp / 'mudo.mp4', duration=25, audio=False)
        res = self.create(mute)
        p = storage.read_project(res['id'])
        self.assertEqual(p['transcription']['state'], 'no_audio')
        job = service.manager.wait(service.request_export(res['id'], 1, {'format': '9:16', 'subtitles': True})['id'], 180)
        self.assertEqual(job['status'], 'done', job.get('error'))
        self.assertTrue(any('transcrição' in w for w in job['result']['warnings']))

    def test_vertical_short_video(self):
        short = make_video(self.tmp / 'curto_vertical.mov', size='360x640', duration=5)
        res = self.create(short)
        p = storage.read_project(res['id'])
        self.assertEqual(p['media']['orientation'], 'vertical')
        self.assertEqual(len(p['clips']), 1)
        self.assertLessEqual(p['clips'][0]['end'], 5.0)

    def test_delete_blocked_while_busy_then_allowed(self):
        res = self.create(self.video)
        job = service.request_export(res['id'], 1, {'format': '9:16', 'framing': 'blur', 'subtitles': False})
        with self.assertRaises(AppError) as ctx:
            service.delete_project(res['id'])
        self.assertEqual(ctx.exception.status, 409)
        service.manager.wait(job['id'], 180)
        service.delete_project(res['id'])
        with self.assertRaises(AppError):
            storage.project_dir(res['id'])


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class UploadValidationTests(ServiceBase):
    def test_bad_extension(self):
        with self.assertRaises(AppError) as ctx:
            service.create_project(iter([b'x']), 'script.exe', OWNED)
        self.assertEqual(ctx.exception.status, 400)

    def test_fake_video_content_rejected_and_cleaned(self):
        before = set(storage.data_dir().iterdir())
        with self.assertRaises(AppError) as ctx:
            service.create_project(iter([b'MZ' + b'\x00' * 9000]), 'falso.mp4', OWNED)
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual({p for p in storage.data_dir().iterdir() if p.name != 'autocortes.db' and 'db-' not in p.name},
                         {p for p in before if p.name != 'autocortes.db' and 'db-' not in p.name})

    def test_empty_file(self):
        with self.assertRaises(AppError) as ctx:
            service.create_project(iter([]), 'vazio.mp4', OWNED)
        self.assertEqual(ctx.exception.code, 'empty')

    def test_size_limit_enforced_while_streaming(self):
        old = config.MAX_UPLOAD_BYTES
        config.MAX_UPLOAD_BYTES = 1000
        try:
            with self.assertRaises(AppError) as ctx:
                service.create_project(iter([b'0' * 600, b'0' * 600]), 'grande.mp4', OWNED)
            self.assertEqual(ctx.exception.status, 413)
            with self.assertRaises(AppError):
                service.create_project(iter([b'0']), 'grande.mp4', OWNED, declared_size=5000)
        finally:
            config.MAX_UPLOAD_BYTES = old

    def test_invalid_options(self):
        for bad in ({'source_rights': 'livre'}, {'target_seconds': '5'}, {'desired_count': 'abc'},
                    {'external_audio': 'talvez'}, {'monetization': '1'}):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                service.validate_options({**OWNED, **bad})

    def test_filename_is_sanitized(self):
        self.assertEqual(service.display_name('..\\..\\Windows\\evil.mp4'), 'evil.mp4')
        self.assertEqual(service.display_name('../../etc/passwd.mp4'), 'passwd.mp4')

    def test_ids_are_validated(self):
        for bad in ('../etc', 'A' * 32, '', '0' * 31):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                storage.project_dir(bad)
        with self.assertRaises(AppError):
            service.get_job('../../x')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='autocortes-rec-'))
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp / 'data'

    def tearDown(self):
        service.shutdown()
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_running_job_becomes_interrupted_after_restart(self):
        pid, folder = storage.new_project_dir()
        storage.write_project(pid, {'id': pid, 'name': 'x', 'filename': 'source.mp4', 'status': 'analyzing',
                                    'created_at': time.time(), 'clips': []})
        mgr = service.startup()
        service.shutdown()
        # simula falha: tarefa ficou "running" no banco
        from app.jobs import JobManager, Handler
        m = JobManager(storage.data_dir() / 'autocortes.db', {'analysis': Handler(lambda c: None)})
        job, _ = m.submit(pid, 'analysis', {})
        m._update(job['id'], status='running')
        with storage.edit_project(pid) as p:
            p['analysis_job'] = job['id']
            p['status'] = 'analyzing'
        m._conn.close()
        mgr = service.startup()
        self.assertEqual(mgr.get(job['id'])['status'], 'interrupted')
        self.assertEqual(storage.read_project(pid)['status'], 'interrupted')
        self.assertIsNotNone(mgr)

    def test_corrupted_project_json_uses_backup(self):
        pid, folder = storage.new_project_dir()
        storage.write_project(pid, {'id': pid, 'name': 'v1'})
        storage.write_project(pid, {'id': pid, 'name': 'v2'})
        (folder / 'project.json').write_text('{corrompido', encoding='utf-8')
        self.assertEqual(storage.read_project(pid)['name'], 'v1')

    def test_concurrent_writes_do_not_corrupt(self):
        pid, _ = storage.new_project_dir()
        storage.write_project(pid, {'id': pid, 'counter': 0})

        def bump():
            for _ in range(50):
                with storage.edit_project(pid) as p:
                    p['counter'] += 1
        threads = [threading.Thread(target=bump) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(storage.read_project(pid)['counter'], 300)

    def test_stale_temp_files_cleaned_on_start(self):
        pid, folder = storage.new_project_dir()
        storage.write_project(pid, {'id': pid, 'name': 'x', 'status': 'ready', 'clips': []})
        (folder / 'clips' / '.tmp' / 'abc').mkdir(parents=True)
        (folder / 'upload.mp4.part').write_bytes(b'x')
        service.startup()
        self.assertFalse((folder / 'clips' / '.tmp').exists())
        self.assertFalse((folder / 'upload.mp4.part').exists())


class SecurityMiddlewareTests(unittest.TestCase):
    def call(self, method='GET', path='/', headers=None):
        reached = []

        async def app(scope, receive, send):
            reached.append(True)
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {'type': 'http.request', 'body': b''}
        hdrs = [(k.encode(), v.encode()) for k, v in {'host': '127.0.0.1:8000', **(headers or {})}.items()]
        mw = LocalSecurityMiddleware(app, allowed_hosts={'127.0.0.1', 'localhost'}, max_body=1000)
        asyncio.run(mw({'type': 'http', 'method': method, 'path': path, 'headers': hdrs}, receive, send))
        return sent[0]['status'], dict(sent[0]['headers']), bool(reached)

    def test_get_allowed_with_security_headers(self):
        status, headers, reached = self.call()
        self.assertEqual(status, 200)
        self.assertIn(b'content-security-policy', headers)
        self.assertEqual(headers[b'x-frame-options'], b'DENY')

    def test_dns_rebinding_blocked(self):
        self.assertEqual(self.call(headers={'host': 'evil.example:8000'})[0], 400)

    def test_csrf_requires_header(self):
        self.assertEqual(self.call('POST', '/api/projects')[0], 403)
        self.assertEqual(self.call('POST', '/api/projects', {'x-autocortes': '1'})[0], 200)

    def test_foreign_origin_blocked(self):
        h = {'x-autocortes': '1', 'origin': 'https://evil.example'}
        self.assertEqual(self.call('DELETE', '/api/projects/x', h)[0], 403)
        self.assertEqual(self.call('POST', '/', {'x-autocortes': '1', 'origin': 'null'})[0], 403)
        self.assertEqual(self.call('POST', '/', {'x-autocortes': '1', 'origin': 'http://127.0.0.1:8000'})[0], 200)

    def test_body_limit(self):
        self.assertEqual(self.call('POST', '/', {'x-autocortes': '1', 'content-length': '5000'})[0], 413)


if __name__ == '__main__':
    unittest.main()
