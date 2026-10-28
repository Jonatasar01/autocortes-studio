"""Liberar cache do projeto: apaga só o que é refeito sozinho (miniaturas, onda, planos, prévias)."""
from __future__ import annotations

import unittest
from unittest import mock

from app import service, storage, timeline
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class CacheCleanupTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'cache.mp4', size='640x360', duration=20, fps=25)

    def test_clear_keeps_project_data_and_caches_come_back(self):
        res = self.create(self.video)
        pid = res['id']
        folder = storage.project_dir(pid)
        timeline.thumbnail(pid)
        (folder / 'direction').mkdir(exist_ok=True)
        (folder / 'direction' / 'track_x.json').write_text('{}', encoding='utf-8')
        (folder / 'preview').mkdir(exist_ok=True)
        (folder / 'preview' / 'clip_01_9x16_v1.mp4').write_bytes(b'0' * 2048)
        with storage.edit_project(pid) as p:
            p['clips'][0]['preview'] = {'file': 'clip_01_9x16_v1.mp4'}
        before = service.cache_usage(pid)
        self.assertGreater(before['total_bytes'], 2048)
        self.assertEqual({x['id'] for x in before['parts']}, {'cache', 'direction', 'preview'})
        kept = {path.name for path in folder.iterdir() if path.is_file()}

        out = service.clear_cache(pid)
        self.assertEqual(out['freed_bytes'], before['total_bytes'])
        self.assertEqual(out['total_bytes'], 0)
        self.assertEqual({path.name for path in folder.iterdir() if path.is_file()}, kept)   # vídeo, projeto...
        p = storage.read_project(pid)
        self.assertTrue(p['clips'])
        self.assertNotIn('preview', p['clips'][0])
        with self.assertRaises(AppError):
            service.preview_path(pid, p['clips'][0]['id'])
        self.assertTrue(timeline.thumbnail(pid).is_file())   # refeita sob demanda

    def test_refuses_while_a_job_is_running(self):
        res = self.create(self.video)
        with mock.patch.object(service._jobs(), 'for_project', return_value=[{'id': 'x'}]):
            with self.assertRaises(AppError) as err:
                service.clear_cache(res['id'])
        self.assertEqual(err.exception.code, 'busy')


if __name__ == '__main__':
    unittest.main()
