"""Detecção de cenas: tenta a GPU (NVDEC) e volta para a CPU sem perder o resultado."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import media
from tests.helpers import HAS_FFMPEG, make_video

MEDIA = {'video_index': 0, 'duration': 10.0}


class SceneFallbackTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='cenas-'))

    def run_with(self, outcomes):
        calls = []

        def fake(args, *, cwd, **_kw):
            calls.append(args)
            outcome = outcomes[len(calls) - 1]
            if outcome == 'fail':
                raise media.FFmpegError('NVDEC indisponível', 'erro')
            (Path(cwd) / 'scenes.txt').write_text('frame:1 pts_time:2.5\nframe:2 pts_time:7.25\n')
            return ''

        with mock.patch.object(media, 'run_ffmpeg', side_effect=fake), \
                mock.patch.dict(os.environ, {'AUTOCORTES_HWACCEL': '1', 'AUTOCORTES_SCENES': '1'}):
            return media.detect_scenes(Path('v.mp4'), MEDIA, self.dir), calls

    def test_gpu_first(self):
        scenes, calls = self.run_with(['ok'])
        self.assertEqual(scenes, [2.5, 7.25])
        self.assertEqual(len(calls), 1)
        self.assertIn('cuda', calls[0])
        self.assertIn('noref', calls[0])

    def test_falls_back_to_cpu(self):
        scenes, calls = self.run_with(['fail', 'ok'])
        self.assertEqual(scenes, [2.5, 7.25])
        self.assertNotIn('cuda', calls[1])
        self.assertIn('noref', calls[1])

    def test_both_fail_returns_no_scenes(self):
        scenes, _ = self.run_with(['fail', 'fail'])
        self.assertEqual(scenes, [])

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_real_video_finds_the_cut(self):
        a = make_video(self.dir / 'a.mp4', size='320x180', duration=3)
        b = make_video(self.dir / 'b.mp4', size='320x180', duration=3,
                       extra=['-vf', 'negate,hue=h=120'])
        joined = self.dir / 'j.mp4'
        (self.dir / 'lista.txt').write_text(f"file '{a.as_posix()}'\nfile '{b.as_posix()}'\n")
        import subprocess
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0',
                        '-i', str(self.dir / 'lista.txt'), '-c', 'copy', str(joined)], check=True)
        info = media.probe_media(joined)
        scenes = media.detect_scenes(joined, info, self.dir / 'work')
        self.assertTrue(any(abs(s - 3.0) < 0.6 for s in scenes), scenes)


if __name__ == '__main__':
    unittest.main()
