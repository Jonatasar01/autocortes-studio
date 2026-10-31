"""Fase 2 da interface: vídeo duplicado (impressão rápida), abrir a pasta dos arquivos e exportação em lote."""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from unittest import mock

from app import config, project_files, service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


class QuickFingerprintTests(unittest.TestCase):
    def test_small_file_is_hashed_whole_and_big_file_by_three_blocks(self):
        import tempfile
        d = Path(tempfile.mkdtemp(prefix='fp-'))
        small = d / 'p.bin'
        small.write_bytes(b'abc' * 1000)
        self.assertEqual(service.quick_fingerprint(small), f'quick:3000:{hashlib.sha256(b"abc" * 1000).hexdigest()}')
        big = d / 'g.bin'
        data = bytes(range(256)) * (13 * 1024 * 1024 // 256)    # 13 MB
        big.write_bytes(data)
        m = service.QUICK_BLOCK
        half = len(data) // 2
        expected = hashlib.sha256(data[:m] + data[half - m // 2:half + m // 2] + data[-m:]).hexdigest()
        self.assertEqual(service.quick_fingerprint(big), f'quick:{len(data)}:{expected}')


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class FlowTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'live repetida.mp4', size='640x360', duration=40, fps=25)

    def test_duplicate_video_is_found_by_its_quick_fingerprint(self):
        res = self.create(self.video)
        quick = service.quick_fingerprint(self.video)
        dup = service.find_duplicate(self.video.stat().st_size, quick)
        self.assertEqual(dup['id'], res['id'])
        self.assertIsNotNone(storage.read_project(res['id']).get('quick_fingerprint'))   # guardada para a próxima
        other = 'quick:%d:%s' % (self.video.stat().st_size, 'f' * 64)
        self.assertIsNone(service.find_duplicate(self.video.stat().st_size, other))
        with self.assertRaises(AppError):
            service.find_duplicate(10, quick)          # tamanho não confere com a impressão

    def test_open_folder_only_opens_app_folders(self):
        res = self.create(self.video)
        with mock.patch.object(project_files, '_reveal') as reveal:   # a abertura de pastas fica em app/project_files.py
            out = service.open_folder(res['id'])
            general = service.open_folder(None)
        opened = [Path(c.args[0]) for c in reveal.call_args_list]
        self.assertEqual(opened[0], Path(out['folder']))
        self.assertTrue(str(opened[0]).startswith(str(storage.project_dir(res['id'])))
                        or str(opened[0]).startswith(str(config.OUTPUT_DIR)))
        self.assertEqual(opened[1], config.OUTPUT_DIR)
        self.assertEqual(general['folder'], str(config.OUTPUT_DIR))

    def test_batch_export_one_file_per_clip_and_format(self):
        res = self.create(self.video)
        p = storage.read_project(res['id'])
        ids = [c['id'] for c in p['clips']]
        out = service.export_all(res['id'], {'formats': ['9:16', '9:16', '16:9'], 'clip_ids': ids, 'subtitles': False})
        self.assertEqual(len(out['jobs']), 2 * len(ids))     # 9:16 repetido vira um só
        for job in out['jobs']:
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        again = service.export_all(res['id'], {'formats': ['9:16'], 'clip_ids': ids[:1], 'subtitles': False})
        self.assertEqual(len(again['jobs']), 1)


if __name__ == '__main__':
    unittest.main()
