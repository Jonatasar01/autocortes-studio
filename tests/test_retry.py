"""'Tentar de novo' numa exportação com erro: mesmas opções, tempos e título atuais do corte."""
from __future__ import annotations

import unittest
from unittest import mock

from app import automation, media, service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class RetryExportTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'retry.mp4', size='640x360', duration=30, fps=25)

    def test_failed_export_is_retried_with_the_current_clip(self):
        res = self.create(self.video)
        clip = storage.read_project(res['id'])['clips'][0]
        with mock.patch.object(media, 'export_clip', side_effect=AppError('Falha simulada.', 500, 'ffmpeg_failed')):
            job = service.request_export(res['id'], clip['id'], {'format': '16:9', 'subtitles': False,
                                                                 'filter': 'pb'})
            failed = service.manager.wait(job['id'], 60)
        self.assertEqual(failed['status'], 'failed')
        service.edit_clip(res['id'], clip['id'], 1.0, 6.0, 'Título novo')
        again = automation.retry(job['id'])
        params = service.manager.get(again['id'])['params']
        self.assertEqual((params['start'], params['end'], params['title']), (1.0, 6.0, 'Título novo'))
        self.assertEqual((params['format'], params['filter']), ('16:9', 'pb'))
        self.assertEqual(service.manager.wait(again['id'], 180)['status'], 'done')

    def test_done_job_cannot_be_retried(self):
        res = self.create(self.video)
        clip = storage.read_project(res['id'])['clips'][0]
        job = service.request_export(res['id'], clip['id'], {'format': '16:9', 'subtitles': False})
        service.manager.wait(job['id'], 180)
        with self.assertRaises(AppError):
            automation.retry(job['id'])


if __name__ == '__main__':
    unittest.main()
