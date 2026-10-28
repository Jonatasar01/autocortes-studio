"""Excluir exportações concluídas e enquadramento escolhido valendo sobre o do plano de edição."""
from __future__ import annotations

import unittest
from unittest import mock

from app import service, settings, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class ExportDeleteTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'del.mp4', size='640x360', duration=20, fps=25)

    def test_delete_one_export_keeps_the_clip_and_other_formats(self):
        settings.update({'output': {'enabled': False}})
        res = self.create(self.video)
        pid = res['id']
        clip = storage.read_project(pid)['clips'][0]
        for fmt in ('16:9', '1:1'):
            job = service.request_export(pid, clip['id'], {'format': fmt, 'subtitles': False})
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        folder = storage.project_dir(pid) / 'clips'
        kept = storage.read_project(pid)['clips'][0]['exports']['1:1']['file']
        gone = storage.read_project(pid)['clips'][0]['exports']['16:9']['file']
        out = service.delete_export(pid, clip['id'], '16:9')
        self.assertGreater(out['freed_bytes'], 0)
        c = storage.read_project(pid)['clips'][0]
        self.assertEqual(set(c['exports']), {'1:1'})
        self.assertFalse((folder / gone).exists())
        self.assertTrue((folder / kept).exists())
        self.assertEqual(c['export_file'], kept)
        with self.assertRaises(AppError):
            service.export_path(pid, clip['id'], '16:9')
        with self.assertRaises(AppError):
            service.delete_export(pid, clip['id'], '16:9')        # já excluída
        service.delete_export(pid, clip['id'], '1:1')
        self.assertNotIn('export_file', storage.read_project(pid)['clips'][0])

    def test_refuses_while_the_same_export_is_running(self):
        res = self.create(self.video)
        clip = storage.read_project(res['id'])['clips'][0]
        running = [{'kind': 'export', 'params': {'clip_id': clip['id'], 'format': '9:16'}}]
        with mock.patch.object(service._jobs(), 'for_project', return_value=running):
            with self.assertRaises(AppError) as err:
                service.delete_export(res['id'], clip['id'], '9:16')
        self.assertEqual(err.exception.code, 'busy')

    def test_chosen_framing_wins_over_the_edit_plan(self):
        res = self.create(self.video)
        pid = res['id']
        clip = storage.read_project(pid)['clips'][0]
        plan = {'settings': {'framing': 'track', 'subtitle_style': 'classica', 'normalize_audio': False}}
        with storage.edit_project(pid) as p:
            p['clips'][0]['edit_plans'] = {'principal': plan}
        sent = []

        def submit(pid_, kind, params, dedupe_key=None):
            sent.append(params)
            return {'id': 'x' * 32, 'params': params, 'status': 'queued', 'kind': kind, 'project_id': pid_}, True
        with mock.patch.object(service, '_plans_stale', return_value=False), \
                mock.patch.object(service._jobs(), 'submit', side_effect=submit), \
                mock.patch.object(service, 'public_job', side_effect=lambda job: job):
            service.request_export(pid, clip['id'], {'format': '9:16', 'framing': 'streamer', 'subtitles': False,
                                                     'edit_plan': 'principal', 'keep_framing': True})
            service.request_export(pid, clip['id'], {'format': '9:16', 'framing': 'streamer', 'subtitles': False,
                                                     'edit_plan': 'principal'})
        self.assertEqual([s['framing'] for s in sent], ['streamer', 'track'])


if __name__ == '__main__':
    unittest.main()
