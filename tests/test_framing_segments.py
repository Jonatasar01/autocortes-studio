"""Enquadramento por trecho: cada parte do corte com fundo desfocado, quadro inteiro ou seguir rosto."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app import media, service
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase

TRACK = {'ok': True, 'shots': [{'start': 0.0, 'end': 6.0, 'layout': 'fill', 'points': [[0.0, 0.3], [6.0, 0.7]]}]}


class CleanSegmentsTests(unittest.TestCase):
    def test_sorted_no_gaps_no_overlap_and_merged(self):
        raw = [{'start': 6, 'end': 9, 'framing': 'blur'}, {'start': 1, 'end': 4, 'framing': 'track'},
               {'start': 3.5, 'end': 6.5, 'framing': 'track'}, {'start': 9, 'end': 30, 'framing': 'fit'}]
        out = service.clean_framing_segments(raw, 12.0)
        # Sobreposição: vale o trecho que começou antes (o de seguir rosto vai até 6,5 s).
        self.assertEqual(out, [{'start': 0.0, 'end': 6.5, 'framing': 'track'},
                               {'start': 6.5, 'end': 9.0, 'framing': 'blur'},
                               {'start': 9.0, 'end': 12.0, 'framing': 'fit'}])

    def test_tiny_segments_are_absorbed_and_empty_stays_empty(self):
        out = service.clean_framing_segments([{'start': 0, 'end': 5, 'framing': 'blur'},
                                              {'start': 5, 'end': 5.2, 'framing': 'track'}], 8.0)
        self.assertEqual(out, [{'start': 0.0, 'end': 8.0, 'framing': 'blur'}])
        self.assertEqual(service.clean_framing_segments([], 8.0), [])

    def test_invalid_framing_is_rejected(self):
        with self.assertRaises(AppError):
            service.clean_framing_segments([{'start': 0, 'end': 3, 'framing': 'zoom'}], 8.0)

    def test_auto_alternates_on_scene_changes(self):
        p = {'scenes': [103.0, 104.0, 112.0, 130.0]}
        clip = {'start': 100.0, 'end': 125.0}
        out = service.auto_framing_segments(p, clip)
        # A cena de 1 s (103→104) não vira trecho; a troca em 130 s fica fora do corte.
        self.assertEqual([(s['start'], s['end'], s['framing']) for s in out],
                         [(0.0, 3.0, 'track'), (3.0, 12.0, 'blur'), (12.0, 25.0, 'track')])

    def test_auto_without_scene_changes_uses_regular_intervals(self):
        out = service.auto_framing_segments({'scenes': []}, {'start': 0.0, 'end': 24.0})
        self.assertEqual([s['framing'] for s in out], ['track', 'blur', 'track', 'blur'])
        self.assertEqual(out[-1]['end'], 24.0)


class MixedGraphTests(unittest.TestCase):
    INFO = {'width': 1280, 'height': 720, 'video_index': 0, 'duration': 6.0}
    SEGS = [{'start': 0, 'end': 2, 'framing': 'track'}, {'start': 2, 'end': 4, 'framing': 'fit'},
            {'start': 4, 'end': 6, 'framing': 'blur'}]

    def test_graph_switches_by_time(self):
        graph, size = media.build_filtergraph(self.INFO, '9:16', 'mix', None, None, TRACK, '', self.SEGS)
        self.assertEqual(size, (1080, 1920))
        self.assertIn('split=3', graph)
        self.assertIn("enable='between(t,2.000,4.000)'", graph)
        self.assertIn("enable='between(t,4.000,6.000)'", graph)
        self.assertTrue(graph.endswith('[vout]'))

    def test_track_without_plan_becomes_blur(self):
        graph, _ = media.build_filtergraph(self.INFO, '9:16', 'mix', None, None, None, '',
                                           [{'start': 0, 'end': 6, 'framing': 'track'}])
        self.assertIn('boxblur', graph)
        self.assertNotIn('crop=w=', graph)

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_ffmpeg_renders_three_framings(self):
        tmp = Path(tempfile.mkdtemp(prefix='mix-'))
        video = make_video(tmp / 'v.mp4', size='1280x720', duration=6)
        info = media.probe_media(video)
        out = media.export_clip(video, info, tmp / 'out', 'mix', 0, 6, fmt='9:16', framing='mix', track=TRACK,
                                segments=self.SEGS, preview=True)
        self.assertTrue(out['validation']['valid'], out['validation'])
        # Quadro inteiro (2–4 s) tem barras pretas em cima; seguir rosto (0–2 s) não.
        def top_luma(t):
            err = subprocess.run(['ffmpeg', '-loglevel', 'info', '-ss', str(t), '-i', str(tmp / 'out' / out['file']),
                                  '-frames:v', '1', '-vf', 'crop=iw:ih/10:0:0,signalstats,metadata=print:key=lavfi.signalstats.YAVG',
                                  '-f', 'null', '-'], capture_output=True, text=True).stderr
            return float(err.split('YAVG=')[-1].split()[0])
        self.assertLess(top_luma(3.0), 20)
        self.assertGreater(top_luma(1.0), 20)


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class FramingApiTests(ServiceBase):
    def test_save_auto_and_export_params(self):
        video = make_video(self.tmp / 'trechos.mp4', size='1280x720', duration=20, fps=25)
        pid = self.create(video)['id']
        clip = service.get_project(pid)['clips'][0]
        saved = service.set_framing_segments(pid, clip['id'], [{'start': 0, 'end': 3, 'framing': 'fit'},
                                                               {'start': 3, 'end': 99, 'framing': 'blur'}])
        clip_len = round(clip['end'] - clip['start'], 3)
        self.assertEqual(saved['segments'][-1]['end'], clip_len)
        job = service.request_export(pid, clip['id'], {'format': '9:16', 'framing': 'mix', 'subtitles': False},
                                     preview=True)
        done = service.manager.wait(job['id'], 240)
        self.assertEqual(done['status'], 'done', done.get('error'))
        self.assertEqual([s['framing'] for s in done['params']['framing_segments']], ['fit', 'blur'])
        auto = service.set_framing_segments(pid, clip['id'], None, auto=True)
        self.assertTrue(auto['segments'])
        self.assertEqual(service.get_project(pid)['clips'][0]['framing_segments'], auto['segments'])


if __name__ == '__main__':
    unittest.main()
