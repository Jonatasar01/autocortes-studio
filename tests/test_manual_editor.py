"""Testes de integração para o editor manual e cortes independentes de transcrição."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg indisponível')
class ManualEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='studio-manual-'))
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp / 'data'
        self.video = make_video(self.tmp / 'sample.mp4', duration=12)
        self.project_id = service.create_project([self.video.read_bytes()], 'sample.mp4',
            {'editing_mode': 'manual', 'source_rights': 'owned', 'autopilot': 'false'})['id']

    def tearDown(self):
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manual_ready_without_transcription(self):
        project = storage.read_project(self.project_id)
        self.assertEqual(project['status'], 'ready')
        self.assertEqual(project['editing_mode'], 'manual')
        self.assertEqual(project['transcription']['state'], 'not_requested')
        self.assertEqual(project['clips'], [])

    def test_create_edit_split_delete_and_stable_ids(self):
        one = service.create_manual_clip(self.project_id, 1, 9, 'Meu momento')
        self.assertEqual(one['id'], 1)
        self.assertEqual(one['selection_mode'], 'manual')
        parts = service.split_manual_clip(self.project_id, one['id'], 4)
        self.assertEqual(parts['second']['id'], 2)
        self.assertEqual(parts['first']['end'], 4)
        service.edit_clip(self.project_id, 1, 0.3, 3.8, 'Corrigido')
        self.assertEqual(service.delete_manual_clip(self.project_id, 2)['deleted'], 2)
        third = service.create_manual_clip(self.project_id, 6, 9, 'Outro')
        self.assertEqual(third['id'], 3)
        persisted = storage.read_project(self.project_id)
        self.assertEqual([c['id'] for c in persisted['clips']], [1, 3])
        self.assertEqual(persisted['clips'][0]['title'], 'Corrigido')

    def test_invalid_intervals_are_rejected(self):
        for start, end in [(3,3.5),(-1,4),(1,30)]:
            with self.assertRaises(AppError):
                service.create_manual_clip(self.project_id, start, end, 'Inválido')
        self.assertEqual(storage.read_project(self.project_id)['clips'], [])

    def test_delete_blocks_active_task(self):
        clip = service.create_manual_clip(self.project_id, 1, 5, 'Em exportação')
        with mock.patch.object(service, 'manager') as jobs:
            jobs.for_project.return_value = [{'params': {'clip_id': clip['id']}}]
            with self.assertRaises(AppError) as ctx:
                service.delete_manual_clip(self.project_id, clip['id'])
            self.assertEqual(ctx.exception.status, 409)
