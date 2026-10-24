"""Exclusão de projeto: atômica (tudo ou nada) e limpeza de exclusões interrompidas."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, storage
from app.errors import AppError

PID = 'a' * 32


class DeleteProjectDirTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix='excluir-'))
        patcher = mock.patch.object(config, 'DATA_DIR', self.data)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = self.data / PID
        (self.folder / 'clips').mkdir(parents=True)
        (self.folder / 'project.json').write_text('{}', encoding='utf-8')
        (self.folder / 'source.mp4').write_bytes(b'x' * 10)

    def test_deletes_whole_folder(self):
        storage.delete_project_dir(PID)
        self.assertFalse(self.folder.exists())
        self.assertEqual(list(self.data.iterdir()), [])

    def test_file_in_use_keeps_project_intact(self):
        with mock.patch.object(Path, 'rename', side_effect=PermissionError('em uso')), \
                mock.patch.object(storage.time, 'sleep'):
            with self.assertRaises(AppError) as ctx:
                storage.delete_project_dir(PID)
        self.assertEqual(ctx.exception.status, 409)
        self.assertTrue((self.folder / 'project.json').is_file())
        self.assertTrue((self.folder / 'source.mp4').is_file())

    def test_startup_sweeps_interrupted_deletion(self):
        leftover = self.data / f'{storage.TRASH_PREFIX}{PID}-0000'
        leftover.mkdir()
        (leftover / 'source.mp4').write_bytes(b'x')
        storage.cleanup_stale_files()
        self.assertFalse(leftover.exists())
        self.assertTrue(self.folder.exists())


class ClassicEditorRemovedTests(unittest.TestCase):
    def test_classic_redirects_and_assets_are_gone(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.get('/classic', follow_redirects=False)
        self.assertEqual(r.status_code, 307)
        self.assertEqual(r.headers['location'], '/')
        for name in ('app.js', 'styles.css'):
            self.assertEqual(client.get('/static/' + name).status_code, 404)
        self.assertNotIn('/classic', client.get('/').text)


if __name__ == '__main__':
    unittest.main()
