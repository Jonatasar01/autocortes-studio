"""Linha do tempo: tira de miniaturas e forma de onda geradas pelo servidor, uma vez e em cache."""
from __future__ import annotations

import base64
import subprocess
import time
import unittest

from app import timeline
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class TimelineAssetsTests(ServiceBase):
    pid = None

    def setUp(self):
        if TimelineAssetsTests.pid is None:   # um projeto para a classe toda
            video = make_video(self.tmp / 'linha.mp4', size='640x360', duration=12, fps=25)
            TimelineAssetsTests.pid = self.create(video)['id']

    def test_filmstrip_is_one_tiled_jpeg_and_cached(self):
        path = timeline.filmstrip(self.pid, 6)
        out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                              '-of', 'csv=p=0', str(path)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, f'{6 * timeline.THUMB_W},{timeline.THUMB_H}')
        stamp = path.stat().st_mtime_ns
        self.assertEqual(timeline.filmstrip(self.pid, 6).stat().st_mtime_ns, stamp)   # não gera de novo
        self.assertEqual(timeline.filmstrip(self.pid, 500).name, f'filmstrip_{timeline.MAX_FRAMES}.jpg')

    def test_waveform_builds_in_background_then_is_ready(self):
        first = timeline.waveform(self.pid)
        self.assertIn(first['state'], ('pending', 'ready'))
        for _ in range(120):
            data = timeline.waveform(self.pid)
            if data['state'] == 'ready':
                break
            time.sleep(0.25)
        self.assertEqual(data['state'], 'ready')
        peaks = base64.b64decode(data['peaks'])
        self.assertEqual(data['bucket'], timeline.WAVE_BUCKET)
        self.assertAlmostEqual(len(peaks), 12 / timeline.WAVE_BUCKET, delta=3)
        self.assertGreater(max(peaks), 150)          # o tom de teste é alto


if __name__ == '__main__':
    unittest.main()
