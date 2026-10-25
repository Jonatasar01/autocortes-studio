"""Prévia da edição: mesma montagem da exportação, meia resolução, sem publicar nada."""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app import reframe, service, storage
from app.errors import AppError
from app.main import app
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase

THIRD_PARTY = {'source_rights': 'third_party', 'third_party_footage': 'yes', 'external_audio': 'unknown',
               'graphics_rights': 'unknown', 'monetization': 'yes'}


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class PreviewTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'previa.mp4', size='1280x720', duration=20, fps=25)

    def test_preview_is_half_size_private_and_replaced(self):
        res = self.create(self.video, THIRD_PARTY)
        pid = res['id']
        clip = service.get_project(pid)['clips'][0]
        opts = {'format': '9:16', 'framing': 'blur', 'subtitles': True, 'subtitle_style': 'dinamica', 'hook': True}
        # A exportação exige confirmar os alertas de direitos; a prévia (local, não publicada) não.
        with self.assertRaises(AppError):
            service.request_export(pid, clip['id'], opts)
        job = service.request_export(pid, clip['id'], opts, preview=True)
        done = service.manager.wait(job['id'], 240)
        self.assertEqual(done['status'], 'done', done.get('error'))
        record = service.get_project(pid)['clips'][0]['preview']
        self.assertTrue(record['preview'])
        self.assertEqual((record['width'], record['height']), (540, 960))
        self.assertTrue(record['validation']['valid'])
        self.assertIsNotNone(record['quality']['subtitles'])
        first = service.preview_path(pid, clip['id'])
        self.assertEqual(first.parent.name, 'preview')
        # Nada de exportação, pacote de publicação ou cópia para a pasta de saída.
        self.assertFalse(service.get_project(pid)['clips'][0].get('exports'))
        self.assertFalse(list((storage.project_dir(pid) / 'clips').glob('*.mp4')))

        response = TestClient(app).get(f'/api/projects/{pid}/clips/{clip["id"]}/preview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'video/mp4')

        job = service.request_export(pid, clip['id'], {**opts, 'framing': 'fit'}, preview=True)
        self.assertEqual(service.manager.wait(job['id'], 240)['status'], 'done')
        second = service.preview_path(pid, clip['id'])
        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())  # só a prévia mais recente fica guardada

    def test_face_plan_is_reused_between_preview_and_export(self):
        res = self.create(self.video)
        clip = service.get_project(res['id'])['clips'][0]
        opts = {'format': '9:16', 'framing': 'track', 'subtitles': False}
        real = reframe.plan
        with mock.patch.object(reframe, 'plan', side_effect=real) as spy:
            for preview in (True, False):
                job = service.request_export(res['id'], clip['id'], opts, preview=preview)
                self.assertEqual(service.manager.wait(job['id'], 240)['status'], 'done')
        self.assertEqual(spy.call_count, 1)

    def test_preview_missing_is_clear(self):
        res = self.create(self.video)
        clip = service.get_project(res['id'])['clips'][0]
        with self.assertRaises(AppError) as ctx:
            service.preview_path(res['id'], clip['id'])
        self.assertEqual(ctx.exception.code, 'no_preview')


if __name__ == '__main__':
    unittest.main()
