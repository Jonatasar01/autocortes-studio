"""Testes ponta a ponta da API FastAPI (requer fastapi + httpx: pip install -r requirements-dev.txt)."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app import config, transcription
from tests.helpers import HAS_FFMPEG, fake_transcript, make_video

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    HAS_FASTAPI = False

H = {'X-AutoCortes': '1'}


@unittest.skipUnless(HAS_FASTAPI and HAS_FFMPEG, 'FastAPI/httpx ou FFmpeg ausente')
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='autocortes-api-'))
        cls.old = config.DATA_DIR
        config.DATA_DIR = cls.tmp / 'data'
        cls.saved = transcription.available, transcription.transcribe
        transcription.available = lambda: True
        transcription.transcribe = lambda wav, dur, on_progress=None, cancel_event=None: (
            fake_transcript(dur), {'state': 'ok', 'message': 'simulada'})
        cls.video = make_video(cls.tmp / 'vídeo de teste.mp4', size='1280x720', duration=30)
        from app.main import app
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)
        transcription.available, transcription.transcribe = cls.saved
        config.DATA_DIR = cls.old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def wait_ready(self, project_id):
        for _ in range(600):
            p = self.client.get(f'/api/projects/{project_id}').json()
            if p['status'] not in ('queued', 'analyzing'):
                return p
            time.sleep(0.2)
        self.fail('análise não terminou')

    def wait_job(self, job_id):
        for _ in range(900):
            j = self.client.get(f'/api/jobs/{job_id}').json()
            if j['status'] in ('done', 'failed', 'cancelled', 'interrupted'):
                return j
            time.sleep(0.2)
        self.fail('tarefa não terminou')

    def test_raw_upload_analyze_export_download(self):
        r = self.client.post('/api/projects?source_rights=owned&external_audio=no&graphics_rights=none'
                             '&third_party_footage=no&monetization=no&target_seconds=10&desired_count=2',
                             content=self.video.read_bytes(),
                             headers={**H, 'X-Filename': 'v%C3%ADdeo.mp4', 'Content-Type': 'application/octet-stream'})
        self.assertEqual(r.status_code, 200, r.text)
        p = self.wait_ready(r.json()['id'])
        self.assertEqual(p['status'], 'ready')
        self.assertEqual(p['name'], 'vídeo.mp4')
        e = self.client.patch(f"/api/projects/{p['id']}/clips/1", json={'start': 0, 'end': 4.5, 'title': 'Teste'}, headers=H)
        self.assertEqual(e.status_code, 200, e.text)
        job = self.client.post(f"/api/projects/{p['id']}/clips/1/export",
                               json={'format': '9:16', 'framing': 'blur', 'subtitles': True}, headers=H)
        self.assertEqual(job.status_code, 200, job.text)
        done = self.wait_job(job.json()['id'])
        self.assertEqual(done['status'], 'done', done)
        dl = self.client.get(f"/api/projects/{p['id']}/clips/1/download?format=9:16")
        self.assertEqual(dl.status_code, 200)
        self.assertGreater(len(dl.content), 1000)
        self.assertEqual(self.client.get(f"/api/projects/{p['id']}/clips/1/subtitles?format=9:16").status_code, 200)
        bundle = self.client.get(f"/api/projects/{p['id']}/bundle")
        self.assertEqual(bundle.status_code, 200, bundle.text if bundle.status_code != 200 else '')
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as z:
            self.assertIn('revisao.json', z.namelist())
            self.assertTrue(any(n.endswith('.mp4') for n in z.namelist()))
        self.assertFalse(list((config.DATA_DIR/p['id']).glob('bundle-*.zip')))
        self.assertEqual(self.client.get('/api/automation').status_code, 200)
        rep = self.client.get(f"/api/projects/{p['id']}/report")
        self.assertEqual(rep.status_code, 200)
        self.assertIn('attachment', rep.headers['content-disposition'])
        src = self.client.get(f"/api/projects/{p['id']}/source", headers={'Range': 'bytes=0-99'})
        self.assertEqual(src.status_code, 206)
        self.assertEqual(len(src.content), 100)
        self.assertEqual(src.headers['content-range'], f'bytes 0-99/{self.video.stat().st_size}')
        outside = self.client.get(f"/api/projects/{p['id']}/source", headers={'Range': 'bytes=999999999-1000000000'})
        self.assertEqual(outside.status_code, 416)

    def test_multipart_upload_compat(self):
        with self.video.open('rb') as fh:
            r = self.client.post('/api/projects', files={'file': ('sample.mp4', fh, 'video/mp4')},
                                 data={'source_rights': 'owned', 'external_audio': 'no', 'desired_count': '2',
                                       'target_seconds': '20'}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.wait_ready(r.json()['id'])['music_review']['status'], 'not_requested')

    def test_security(self):
        self.assertEqual(self.client.post('/api/projects', content=b'x').status_code, 403)
        self.assertEqual(self.client.post('/api/projects', content=b'x',
                                          headers={**H, 'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/', headers={'Host': 'evil.example'}).status_code, 400)
        self.assertIn("default-src 'self'", self.client.get('/').headers['content-security-policy'])
        self.assertEqual(self.client.get('/static/../main.py').status_code, 404)
        self.assertEqual(self.client.get('/api/projects/..%2F..%2Fetc').status_code, 404)
        self.assertEqual(self.client.get('/api/docs').status_code, 404)

    def test_narration_endpoints_validate(self):
        bad_style = self.client.post('/api/projects/' + '0' * 32 + '/clips/1/narration',
                                     json={'style': 'outro', 'consent': True}, headers=H)
        self.assertEqual(bad_style.status_code, 422)
        empty = self.client.put('/api/projects/' + '0' * 32 + '/clips/1/narration', json={'segments': []}, headers=H)
        self.assertEqual(empty.status_code, 422)
        self.assertIn('openai_configured', self.client.get('/api/health').json())

    def test_narration_api_complete_flow(self):
        from unittest import mock
        from app import narration, storage
        from tests.test_narration import FakeOpenAI
        r = self.client.post('/api/projects?source_rights=owned&target_seconds=10&desired_count=1',
                             content=self.video.read_bytes(), headers={**H, 'X-Filename': 'narracao.mp4'})
        self.assertEqual(r.status_code, 200)
        p = self.wait_ready(r.json()['id'])
        route = f"/api/projects/{p['id']}/clips/1/narration"
        with mock.patch.dict('os.environ', {'OPENAI_API_KEY': 'test'}), mock.patch.object(narration, '_request', FakeOpenAI()):
            self.assertEqual(self.client.post(route, json={'consent': False}, headers=H).status_code, 409)
            job = self.client.post(route, json={'consent': True}, headers=H)
            self.assertEqual(job.status_code, 200)
            self.assertEqual(self.wait_job(job.json()['id'])['status'], 'done')
            saved = self.client.put(route, json={'segments': [{'start': 0, 'end': 2, 'text': 'Texto revisado.'}]}, headers=H)
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()['edited_manually'])
        self.assertTrue(storage.read_project(p['id'])['openai_consents'])

    def test_automation_endpoints(self):
        self.assertEqual(self.client.get('/api/settings').status_code, 200)
        bad = self.client.put('/api/settings', json={'autopilot': {'min_score': 999}}, headers=H)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.put('/api/settings', json={'autopilot': {'enabled': False}}).status_code, 403)
        ok = self.client.put('/api/settings', json={'autopilot': {'enabled': False, 'formats': ['16:9']},
                                                   'output': {'enabled': False}}, headers=H)
        self.assertEqual(ok.status_code, 200, ok.text)
        r = self.client.post('/api/projects?source_rights=owned&external_audio=no&target_seconds=10&desired_count=2'
                             '&autopilot=false', content=self.video.read_bytes(), headers={**H, 'X-Filename': 'lote.mp4'})
        self.assertEqual(r.status_code, 200, r.text)
        pid = self.wait_ready(r.json()['id'])['id']
        batch = self.client.post(f'/api/projects/{pid}/export-all', json={'formats': ['1:1'], 'subtitles': False}, headers=H)
        self.assertEqual(batch.status_code, 200, batch.text)
        for job in batch.json()['jobs']:
            self.assertEqual(self.wait_job(job['id'])['status'], 'done')
        pub = self.client.get(f'/api/projects/{pid}/clips/1/publication?format=1:1')
        self.assertEqual(pub.status_code, 200)
        self.assertIn('TÍTULO SUGERIDO', pub.text)
        self.assertEqual(self.client.post(f'/api/projects/{pid}/autopilot/resume', json={'acknowledge_rights': True},
                                          headers=H).status_code, 409)
        run = self.client.post(f'/api/projects/{pid}/autopilot/run', headers=H)
        self.assertEqual(run.status_code, 200, run.text)

    def test_invalid_inputs(self):
        bad = self.client.post('/api/projects?source_rights=owned', content=b'\x00' * 5000,
                               headers={**H, 'X-Filename': 'falso.mp4'})
        self.assertEqual(bad.status_code, 422)
        self.assertNotIn(str(self.tmp), bad.text)
        ext = self.client.post('/api/projects', content=b'x', headers={**H, 'X-Filename': 'a.exe'})
        self.assertEqual(ext.status_code, 400)
        nan = self.client.patch('/api/projects/' + '0' * 32 + '/clips/1',
                                content=b'{"start": NaN, "end": 3, "title": "x"}',
                                headers={**H, 'Content-Type': 'application/json'})
        self.assertIn(nan.status_code, (404, 422))
        fmt = self.client.post('/api/projects/' + '0' * 32 + '/clips/1/export', json={'format': '4:3'}, headers=H)
        self.assertEqual(fmt.status_code, 422)


if __name__ == '__main__':
    unittest.main()
