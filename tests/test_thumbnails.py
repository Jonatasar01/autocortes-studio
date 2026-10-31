"""Capa automática: quadro do corte exportado com o título grande por cima."""
from __future__ import annotations

import subprocess
import unittest

from app import service, storage, thumbnails
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


class LinesTests(unittest.TestCase):
    def test_title_in_up_to_three_lines(self):
        self.assertEqual(thumbnails._lines('olha o que aconteceu', True), 'OLHA O QUE\nACONTECEU')
        long = thumbnails._lines('um título enorme que não cabe de jeito nenhum em três linhas da capa', True)
        self.assertEqual(len(long.split('\n')), 3)
        self.assertTrue(long.endswith('…'))


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class ThumbnailTests(ServiceBase):
    def test_cover_from_the_exported_clip(self):
        pid = self.create(make_video(self.tmp / 'capa.mp4', size='640x360', duration=8, fps=25))['id']
        clip = storage.read_project(pid)['clips'][0]
        with self.assertRaises(AppError):
            thumbnails.make(pid, clip['id'], '16:9')   # sem exportar ainda
        job = service.request_export(pid, clip['id'], {'format': '16:9', 'framing': 'fit', 'subtitles': False})
        self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        path = thumbnails.make(pid, clip['id'], '16:9')
        self.assertEqual(path.read_bytes()[:2], bytes([0xFF, 0xD8]))   # JPEG
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                                str(path)], capture_output=True, text=True, check=True).stdout.strip()
        rec = storage.read_project(pid)['clips'][0]['exports']['16:9']
        self.assertEqual(probe, f"{rec['width']},{rec['height']}")
        self.assertEqual(rec['thumbnail_file'], path.name)
        self.assertEqual(thumbnails.make(pid, clip['id'], '16:9'), path)   # reaproveita
        service.delete_export(pid, clip['id'], '16:9')
        self.assertFalse(path.exists())   # sai junto com a exportação


if __name__ == '__main__':
    unittest.main()
