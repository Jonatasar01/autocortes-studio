"""Página inicial: projetos, tarefas em andamento, últimos cortes exportados, dependências e miniatura."""
from __future__ import annotations

import unittest

from app import dashboard, service, storage, timeline
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class DashboardTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'live de teste.mp4', size='1280x720', duration=30, fps=25)

    def test_overview_lists_projects_exports_and_dependencies(self):
        res = self.create(self.video)
        export = service.request_export(res['id'], 1, {'format': '9:16', 'subtitles': False})
        self.assertEqual(service.manager.wait(export['id'], 180)['status'], 'done')
        preview = service.request_export(res['id'], 1, {'format': '9:16', 'subtitles': False}, preview=True)
        service.manager.wait(preview['id'], 180)

        d = dashboard.overview()
        self.assertIn(res['id'], [p['id'] for p in d['projects']])
        self.assertEqual(d['active_jobs'], [])
        mine = [x for x in d['recent_exports'] if x['project_id'] == res['id']]
        self.assertEqual(len(mine), 1)                     # a prévia não conta como exportação
        self.assertEqual((mine[0]['clip_id'], mine[0]['format']), (1, '9:16'))
        ids = {x['id'] for x in d['dependencies']}
        self.assertTrue({'ffmpeg', 'legendas', 'transcricao', 'rostos', 'disco'} <= ids)
        self.assertTrue(all(x['fix'] for x in d['dependencies']))   # todo item diz o que fazer se faltar
        self.assertIn('max_upload_gb', d['limits'])

        jobs = dashboard.jobs(20)
        self.assertNotIn(preview['id'], [j['id'] for j in jobs])
        mine = next(j for j in jobs if j['id'] == export['id'])
        self.assertEqual((mine['kind_label'], mine['project_id']), ('Exportação', res['id']))

    def test_deleted_export_file_leaves_the_list(self):
        res = self.create(self.video)
        export = service.request_export(res['id'], 1, {'format': '16:9', 'subtitles': False})
        done = service.manager.wait(export['id'], 180)
        (storage.project_dir(res['id']) / 'clips' / done['result']['file']).unlink()
        self.assertNotIn((res['id'], 1, '16:9'), [(x['project_id'], x['clip_id'], x['format'])
                                                   for x in dashboard.recent_exports(50)])

    def test_thumbnail_is_generated_once(self):
        res = self.create(self.video)
        thumb = timeline.thumbnail(res['id'])
        self.assertTrue(thumb.is_file())
        self.assertEqual(thumb.read_bytes()[:2], b'\xff\xd8')   # JPEG
        stamp = thumb.stat().st_mtime_ns
        self.assertEqual(timeline.thumbnail(res['id']).stat().st_mtime_ns, stamp)


if __name__ == '__main__':
    unittest.main()
