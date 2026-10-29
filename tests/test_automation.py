"""Automação (v2.2): configurações, piloto automático, pasta de entrada/saída, exportar todos,
novas tentativas automáticas e encurtamento de pausas. FFmpeg real."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import autopilot, config, media, publish, service, settings, storage, transcription, watcher
from app.errors import AppError
from app.jobs import Handler, JobManager
from tests.helpers import HAS_FFMPEG, fake_transcript, make_video

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


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='autocortes-set-'))
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp / 'data'

    def tearDown(self):
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_are_conservative(self):
        s = settings.load()
        self.assertFalse(s['autopilot']['enabled'])
        self.assertFalse(s['watch']['enabled'])
        self.assertFalse(s['autopilot']['include_temporal'])
        self.assertEqual(s['upload_defaults']['source_rights'], 'unknown')

    def test_update_validates_and_persists(self):
        s = settings.update({'autopilot': {'enabled': True, 'formats': ['9:16', '1:1', '9:16'], 'min_score': 70}})
        self.assertEqual(s['autopilot']['formats'], ['9:16', '1:1'])
        self.assertEqual(settings.load()['autopilot']['min_score'], 70)
        for bad in ({'autopilot': {'min_score': 500}}, {'autopilot': {'formats': []}},
                    {'autopilot': {'formats': ['4:3']}}, {'upload_defaults': {'source_rights': 'livre'}},
                    {'autopilot': {'enabled': 'sim'}}, 'texto'):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                settings.update(bad)

    def test_unknown_keys_ignored_and_no_paths_writable(self):
        s = settings.update({'paths': {'watch_dir': '/etc'}, 'chave_desconhecida': 1})
        self.assertNotIn('chave_desconhecida', s)
        self.assertNotEqual(settings.public()['paths']['watch_dir'], '/etc')

    def test_choose_respects_score_and_temporal(self):
        cfg = settings.defaults()['autopilot']
        clips = [{'id': 1, 'rank': 1, 'score': 80, 'selection_mode': 'transcricao'},
                 {'id': 2, 'rank': 2, 'score': 40, 'selection_mode': 'transcricao'},
                 {'id': 3, 'rank': 3, 'score': None, 'selection_mode': 'temporal'}]
        chosen, skipped = autopilot.choose(clips, cfg)
        self.assertEqual([c['id'] for c in chosen], [1])
        self.assertEqual(len(skipped), 2)
        chosen, _ = autopilot.choose(clips, {**cfg, 'include_temporal': True, 'min_score': 0})
        self.assertEqual([c['id'] for c in chosen], [1, 2, 3])


class PauseTests(unittest.TestCase):
    def test_pause_intervals_and_remap(self):
        words = [{'start': 0.2, 'end': 1.9, 'text': 'a', 'words': [{'start': 0.2, 'end': 1.9, 'word': 'a'}]},
                 {'start': 4.6, 'end': 5.9, 'text': 'b', 'words': [{'start': 4.6, 'end': 5.9, 'word': 'b'}]}]
        keep = media.pause_intervals(words, 0, 7)
        self.assertEqual(len(keep), 2)
        self.assertLess(sum(b - a for a, b in keep), 5)
        self.assertAlmostEqual(media.remap_time(4.6, keep), keep[0][1] - keep[0][0] + 4.6 - keep[1][0], places=3)
        self.assertIsNone(media.pause_intervals([{'start': 0, 'end': 7, 'text': 'contínuo'}], 0, 7))

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_shortened_export_is_valid_and_synced(self):
        d = Path(tempfile.mkdtemp(prefix='autocortes-pause-'))
        try:
            src = d / 'p.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=30:d=10',
                            '-f', 'lavfi', '-i', "aevalsrc='if(lt(t,2)+between(t,4.5,6)+gte(t,9),sin(2*PI*440*t),0)':d=10",
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(src)], check=True)
            info = media.probe_media(src)
            keep = media.silence_intervals(src, info, 0, 10)
            self.assertIsNotNone(keep)
            r = media.export_clip(src, info, d / 'out', 'clip_01', 0, 10, fmt='16:9', keep=keep)
            self.assertTrue(r['validation']['valid'], r['validation'])
            self.assertLess(r['validation']['duration'], 7)
            self.assertGreater(r['shortened']['removed_seconds'], 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class RetryTests(unittest.TestCase):
    def test_transient_error_is_retried_then_succeeds(self):
        tmp = Path(tempfile.mkdtemp(prefix='autocortes-retry-'))
        calls = []

        def flaky(ctx):
            calls.append(time.time())
            if len(calls) < 3:
                raise AppError('Sem conexão.', 502, 'openai_unreachable')
            return {'ok': True}

        def permanent(ctx):
            raise AppError('Chave inválida.', 502, 'openai_error')
        old = config.JOB_RETRY_BASE_SECONDS
        config.JOB_RETRY_BASE_SECONDS = 0
        m = JobManager(tmp / 'db.sqlite', {
            'flaky': Handler(flaky, retry_codes={'openai_unreachable'}, max_retries=2),
            'perm': Handler(permanent, retry_codes={'openai_unreachable'}, max_retries=2)})
        try:
            m.start()
            job, _ = m.submit('p', 'flaky', {})
            done = m.wait(job['id'], 30)
            self.assertEqual(done['status'], 'done')
            self.assertEqual(done['attempts'], 2)
            bad, _ = m.submit('p', 'perm', {})
            self.assertEqual(m.wait(bad['id'], 30)['status'], 'failed')
            self.assertTrue(any('Nova tentativa' in e['message'] for e in m.events(job['id'])))
        finally:
            config.JOB_RETRY_BASE_SECONDS = old
            m.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_old_database_is_migrated(self):
        import sqlite3
        tmp = Path(tempfile.mkdtemp(prefix='autocortes-mig-'))
        try:
            conn = sqlite3.connect(tmp / 'db.sqlite')
            conn.executescript('CREATE TABLE jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, '
                               'params TEXT NOT NULL, status TEXT NOT NULL, stage TEXT, progress REAL DEFAULT 0, '
                               'message TEXT, error TEXT, result TEXT, dedupe_key TEXT, created REAL NOT NULL, '
                               'started REAL, finished REAL);')
            conn.close()
            m = JobManager(tmp / 'db.sqlite', {'x': Handler(lambda c: {})})
            m.start()
            job, _ = m.submit('p', 'x', {})
            self.assertEqual(m.wait(job['id'], 10)['status'], 'done')
            m.stop()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PublishTests(unittest.TestCase):
    def test_package_mentions_pending_rights(self):
        project = {'id': 'a' * 32, 'name': 'Meu vídeo: teste?.mp4',
                   'rights': {'items': [{'label': 'Música', 'status': 'nao_verificado', 'status_label': 'Não verificado'}]}}
        clip = {'id': 2, 'title': 'Título', 'description': 'O segredo do café é a água. A água muda tudo no café.'}
        record = {'file': 'clip_02_9x16_v1.mp4', 'srt_file': None, 'width': 1080, 'height': 1920, 'format': '9:16',
                  'warnings': ['Aviso X']}
        text = publish.package(project, clip, record)
        self.assertIn('Título', text)
        self.assertIn('Aviso X', text)
        self.assertIn('Pendências de direitos', text)
        self.assertIn('#shorts', text)
        self.assertNotIn('?', publish.output_folder(project).name)


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class AutomationFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='autocortes-auto-'))
        cls.saved = (config.DATA_DIR, config.WATCH_DIR, config.OUTPUT_DIR, transcription.available,
                     transcription.transcribe, config.JOB_RETRY_BASE_SECONDS)
        config.DATA_DIR = cls.tmp / 'data'
        config.WATCH_DIR = cls.tmp / 'entrada'
        config.OUTPUT_DIR = cls.tmp / 'saida'
        config.JOB_RETRY_BASE_SECONDS = 0
        transcription.available = lambda: True
        transcription.transcribe = lambda wav, dur, on_progress=None, cancel_event=None: (
            fake_transcript(dur, [f'Assunto {n}: a equipe de {w} trouxe novidades importantes hoje.'
                                  for n, w in enumerate(['clima', 'mercado', 'futebol', 'saúde', 'música', 'cinema',
                                                         'ciência', 'turismo', 'tecnologia', 'culinária'])]),
            {'state': 'ok', 'message': 'simulada'})
        cls.video = make_video(cls.tmp / 'Gravação própria.mp4', size='1280x720', duration=45)
        service.startup()
        watcher.stop()  # nos testes a varredura é chamada manualmente

    @classmethod
    def tearDownClass(cls):
        service.shutdown()
        (config.DATA_DIR, config.WATCH_DIR, config.OUTPUT_DIR, transcription.available, transcription.transcribe,
         config.JOB_RETRY_BASE_SECONDS) = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def settle(self, pid):
        def finished():
            ap = storage.read_project(pid).get('autopilot') or {}
            active = service._jobs().for_project(pid, active_only=True)
            return ap if ap.get('status') not in (None, 'aguardando_analise', 'executando') and not active else None
        return wait_until(finished)

    def upload(self, form):
        with self.video.open('rb') as fh:
            return service.create_project(iter(lambda: fh.read(1 << 20), b''), self.video.name, form)

    def test_watch_folder_autopilot_exports_to_output(self):
        settings.update({'upload_defaults': {**OWNED, 'target_seconds': 12, 'desired_count': 3},
                         'autopilot': {'enabled': False, 'max_clips': 2, 'min_score': 0, 'formats': ['9:16', '16:9'],
                                       'framing': 'fit', 'shorten_pauses': True},
                         'watch': {'enabled': True, 'autopilot': True}, 'output': {'enabled': True}})
        config.WATCH_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.video, config.WATCH_DIR / 'Entrevista nova.mp4')
        (config.WATCH_DIR / 'baixando.mp4.part').write_bytes(b'x')
        (config.WATCH_DIR / 'quebrado.mp4').write_bytes(b'\x00' * 5000)
        actions = []
        for _ in range(4):
            actions += watcher.scan_once(stable_checks=1)
        imported = [a for a in actions if a['status'] == 'imported']
        self.assertEqual(len(imported), 1, actions)
        self.assertTrue(any(a['status'] == 'failed' for a in actions))
        self.assertTrue((config.WATCH_DIR / 'falhas' / 'quebrado.mp4.erro.txt').is_file())
        self.assertTrue((config.WATCH_DIR / 'baixando.mp4.part').exists(), 'arquivos incompletos não são tocados')
        self.assertTrue((config.WATCH_DIR / 'processados' / 'Entrevista nova.mp4').is_file())
        pid = imported[0]['project_id']
        ap = self.settle(pid)
        self.assertEqual(ap['status'], 'concluido', ap)
        p = storage.read_project(pid)
        self.assertEqual(p['origin'], 'pasta_entrada')
        exported = [c for c in p['clips'] if c.get('exports')]
        self.assertEqual(len(exported), 2)
        self.assertTrue(all(set(c['exports']) == {'9:16', '16:9'} for c in exported))
        record = exported[0]['exports']['9:16']
        self.assertTrue(record['validation']['valid'])
        self.assertTrue(record['package_file'])
        out_files = list(publish.output_folder(p).iterdir())
        self.assertEqual(sum(f.suffix == '.mp4' for f in out_files), 4)
        self.assertTrue(any(f.name.endswith('publicação.txt') for f in out_files))
        self.assertFalse(any(f.name.endswith('.part') for f in out_files))
        # mesmo arquivo de novo = duplicata, sem novo projeto
        shutil.copy(self.video, config.WATCH_DIR / 'Entrevista nova (cópia).mp4')
        again = []
        for _ in range(3):
            again += watcher.scan_once(stable_checks=1)
        self.assertEqual([a['status'] for a in again], ['duplicate'])
        settings.update({'watch': {'enabled': False}})

    def test_high_risk_pauses_until_user_confirms(self):
        settings.update({'autopilot': {'enabled': True, 'max_clips': 1, 'min_score': 0, 'formats': ['1:1'],
                                       'shorten_pauses': False}, 'output': {'enabled': False}})
        res = self.upload({**OWNED, 'source_rights': 'third_party', 'target_seconds': '12', 'desired_count': '2'})
        ap = self.settle(res['id'])
        self.assertEqual(ap['status'], 'pausado_direitos')
        self.assertFalse(any(c.get('exports') for c in storage.read_project(res['id'])['clips']))
        with self.assertRaises(AppError):
            service.autopilot_resume(res['id'], False)
        service.autopilot_resume(res['id'], True)
        ap = self.settle(res['id'])
        self.assertEqual(ap['status'], 'concluido')
        acks = storage.read_project(res['id'])['rights_acknowledgements']
        self.assertTrue(any(a.get('source') == 'piloto automático' for a in acks))

    def test_upload_override_disables_autopilot_and_run_now(self):
        settings.update({'autopilot': {'enabled': True, 'formats': ['1:1'], 'max_clips': 1, 'min_score': 0}})
        res = self.upload({**OWNED, 'target_seconds': '12', 'desired_count': '2', 'autopilot': 'false'})
        service.manager.wait(res['job']['id'], 180)
        self.assertIsNone(storage.read_project(res['id']).get('autopilot'))
        service.autopilot_run(res['id'])
        ap = self.settle(res['id'])
        self.assertEqual(ap['status'], 'concluido')
        first_run = ap['run_id']
        service.autopilot_run(res['id'])
        second = self.settle(res['id'])
        self.assertEqual(second['status'], 'concluido')
        self.assertNotEqual(first_run, second['run_id'])
        runs = {j['params'].get('autopilot_run_id') for j in service.manager.for_project(res['id'])
                if j['params'].get('autopilot')}
        self.assertEqual(runs, {first_run, second['run_id']})

    def test_export_all(self):
        settings.update({'autopilot': {'enabled': False}, 'output': {'enabled': False}})
        res = self.upload({**OWNED, 'target_seconds': '12', 'desired_count': '2'})
        service.manager.wait(res['job']['id'], 180)
        out = service.export_all(res['id'], {'formats': ['16:9', '1:1'], 'subtitles': False})
        self.assertEqual(len(out['jobs']), 2 * len(storage.read_project(res['id'])['clips']))
        for job in out['jobs']:
            self.assertEqual(service.manager.wait(job['id'], 240)['status'], 'done')
        with self.assertRaises(AppError):
            service.export_all(res['id'], {'formats': ['4:3']})


if __name__ == '__main__':
    unittest.main()
