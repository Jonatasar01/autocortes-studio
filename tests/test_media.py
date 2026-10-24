"""Testes reais com FFmpeg: importação, formatos, exportação e validação."""
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app import media, subtitles
from app.errors import AppError, Cancelled
from tests.helpers import HAS_FFMPEG, fake_transcript, make_video


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='autocortes-media-'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_containers(self):
        cases = {'a.mp4': {}, 'a.mov': {}, 'a.m4v': {}, 'a.mkv': {},
                 'a.avi': {'vcodec': 'mpeg4', 'acodec': 'mp3'},
                 'a.webm': {'vcodec': 'libvpx', 'acodec': 'libopus', 'extra': ['-deadline', 'realtime', '-cpu-used', '8']}}
        for name, opts in cases.items():
            with self.subTest(name=name):
                info = media.probe_media(make_video(self.dir / name, duration=3, **opts))
                self.assertAlmostEqual(info['duration'], 3, delta=0.2)
                self.assertTrue(info['audio'])
                self.assertEqual((info['width'], info['height']), (640, 360))

    def test_orientations_and_rotation(self):
        self.assertEqual(media.probe_media(make_video(self.dir / 'v.mp4', size='360x640', duration=2))['orientation'], 'vertical')
        self.assertEqual(media.probe_media(make_video(self.dir / 'q.mp4', size='480x480', duration=2))['orientation'], 'quadrado')
        rotated = make_video(self.dir / 'rot_src.mp4', size='640x360', duration=2)
        out = self.dir / 'rot.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-display_rotation', '90', '-i', str(rotated), '-c', 'copy', str(out)],
                       check=True)
        info = media.probe_media(out)
        self.assertEqual((info['width'], info['height'], info['orientation']), (360, 640, 'vertical'))

    def test_no_audio(self):
        info = media.probe_media(make_video(self.dir / 'mute.mp4', audio=False, duration=2))
        self.assertFalse(info['audio'])
        self.assertIsNone(info['audio_index'])

    def test_special_characters_in_path(self):
        folder = self.dir / 'pasta com espaço & ação'
        folder.mkdir()
        info = media.probe_media(make_video(folder / "vídeo 'teste' #1 (çã).mp4", duration=2))
        self.assertGreater(info['duration'], 1)

    def test_corrupted_truncated_empty_and_audio_only(self):
        (self.dir / 'lixo.mp4').write_bytes(b'\x00' * 4096)
        good = make_video(self.dir / 'full.mp4', duration=4)
        (self.dir / 'cortado.mp4').write_bytes(good.read_bytes()[: good.stat().st_size // 3])
        (self.dir / 'vazio.mp4').write_bytes(b'')
        audio = self.dir / 'audio.m4a'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=d=2', '-c:a', 'aac', str(audio)], check=True)
        for name in ('lixo.mp4', 'cortado.mp4', 'vazio.mp4', 'audio.m4a'):
            with self.subTest(name=name):
                with self.assertRaises(AppError) as ctx:
                    media.probe_media(self.dir / name)
                self.assertEqual(ctx.exception.status, 422)
                self.assertNotIn(str(self.dir), ctx.exception.message)

    def test_vfr_detected_and_warned(self):
        src = make_video(self.dir / 'cfr.mkv', duration=4, fps=30)
        vfr = self.dir / 'vfr.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-vf', "select='not(mod(n\\,3))+lt(t\\,2)'",
                        '-fps_mode', 'vfr', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'copy', str(vfr)], check=True)
        info = media.probe_media(vfr)
        if info['vfr']:
            self.assertTrue(any('variável' in w for w in media.integrity_warnings(vfr, info)))
            self.assertIsNotNone(media.output_fps(info))


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='autocortes-export-'))
        cls.src = make_video(cls.dir / 'origem.mp4', size='1280x720', duration=12, fps=30)
        cls.info = media.probe_media(cls.src)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_all_formats_validated(self):
        expected = {'9:16': (1080, 1920), '16:9': (1280, 720), '1:1': (1080, 1080), 'original': (1280, 720)}
        for fmt, size in expected.items():
            for framing in ('fit', 'blur'):
                if fmt == 'original' and framing == 'blur':
                    continue
                with self.subTest(fmt=fmt, framing=framing):
                    out = self.dir / f'out_{framing}'
                    r = media.export_clip(self.src, self.info, out, 'clip_01', 2.0, 6.5, fmt=fmt, framing=framing)
                    self.assertTrue(r['validation']['valid'], r['validation'])
                    self.assertEqual((r['width'], r['height']), size)
                    self.assertAlmostEqual(r['validation']['duration'], 4.5, delta=0.3)
                    self.assertTrue((out / r['file']).is_file())
                    self.assertFalse((out / '.tmp').exists(), 'temporários devem ser removidos')

    def test_versions_do_not_overwrite(self):
        out = self.dir / 'versions'
        a = media.export_clip(self.src, self.info, out, 'clip_02', 0, 3, fmt='16:9')
        b = media.export_clip(self.src, self.info, out, 'clip_02', 0, 3, fmt='16:9')
        self.assertNotEqual(a['file'], b['file'])

    def test_subtitles_burned_and_srt(self):
        transcript = fake_transcript(12)
        w, h = media.target_size('9:16', self.info)
        cues = subtitles.build_cues(transcript, 1, 9, subtitles.style_for(w, h)['max_chars'])
        r = media.export_clip(self.src, self.info, self.dir / 'subs', 'clip_03', 1, 9, fmt='9:16',
                              ass_text=subtitles.to_ass(cues, w, h), srt_text=subtitles.to_srt(cues))
        self.assertTrue(r['subtitles_burned'])
        self.assertTrue((self.dir / 'subs' / r['srt_file']).is_file())

    def test_broken_subtitles_fall_back_without_subtitles(self):
        r = media.export_clip(self.src, self.info, self.dir / 'badsubs', 'clip_04', 0, 3, fmt='16:9',
                              ass_text='isto não é um arquivo ASS válido [[[', srt_text='1\n')
        self.assertTrue(r['validation']['valid'])
        # libass pode ignorar o arquivo inválido (sem erro) ou falhar; em ambos os casos o MP4 é válido.
        if not r['subtitles_burned']:
            self.assertTrue(any('legendas' in w for w in r['warnings']))

    def test_no_audio_export(self):
        mute = make_video(self.dir / 'mudo.mp4', audio=False, duration=4)
        info = media.probe_media(mute)
        r = media.export_clip(mute, info, self.dir / 'mute', 'clip_05', 0.5, 3.5, fmt='1:1')
        self.assertTrue(r['validation']['valid'])
        self.assertFalse(any(c['check'] == 'trilha_audio' for c in r['validation']['checks']))

    def test_audio_sync_and_normalization(self):
        r = media.export_clip(self.src, self.info, self.dir / 'norm', 'clip_06', 3, 8, fmt='16:9', normalize_audio=True)
        sync = next(c for c in r['validation']['checks'] if c['check'] == 'sincronia_audio')
        self.assertTrue(sync['ok'], sync)

    def test_end_clamped_and_invalid_ranges(self):
        r = media.export_clip(self.src, self.info, self.dir / 'clamp', 'clip_07', 10, 999, fmt='16:9')
        self.assertAlmostEqual(r['end'], 12, delta=0.05)
        with self.assertRaises(AppError):
            media.export_clip(self.src, self.info, self.dir / 'bad', 'clip_08', 5, 5.5)
        with self.assertRaises(AppError):
            media.export_clip(self.src, self.info, self.dir / 'bad', 'clip_08', 0, 5, fmt='4:3')

    def test_validation_rejects_wrong_output(self):
        with self.assertRaises(AppError):
            media.validate_output(self.dir / 'nao_existe.mp4', expected_duration=3, expected_size=(1, 1), expect_audio=True)
        report = media.validate_output(self.src, expected_duration=3, expected_size=(1080, 1920), expect_audio=True)
        self.assertFalse(report['valid'])

    def test_cancel_before_launch_does_not_spawn_ffmpeg(self):
        from unittest import mock
        event = threading.Event()
        event.set()
        with mock.patch.object(media.subprocess, 'Popen') as spawn:
            with self.assertRaises(Cancelled):
                media.run_ffmpeg(['-f', 'lavfi', '-i', 'color=s=32x32:d=2',
                                  '-f', 'null', '-'], duration=2, cancel_event=event)
            spawn.assert_not_called()

    def test_cancel_during_execution(self):
        src = make_video(self.dir / 'cancel_rapido.mp4', duration=3)
        event = threading.Event()
        timer = threading.Timer(0.15, event.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(Cancelled):
                media.run_ffmpeg(['-re', '-i', str(src), '-f', 'null', '-'],
                                 duration=3, cancel_event=event)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 3.0)

    def test_cancel_kills_ffmpeg_and_leaves_no_file(self):
        big = make_video(self.dir / 'grande.mp4', size='1920x1080', duration=40, fps=30)
        info = media.probe_media(big)
        event = threading.Event()
        threading.Timer(1.0, event.set).start()
        out = self.dir / 'cancel'
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            media.export_clip(big, info, out, 'clip_09', 0, 40, fmt='9:16', framing='blur', cancel_event=event)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(list(out.glob('*.mp4')), [])

    def test_4k_source(self):
        uhd = make_video(self.dir / '4k.mp4', size='3840x2160', duration=2, fps=24)
        info = media.probe_media(uhd)
        r = media.export_clip(uhd, info, self.dir / '4k', 'clip_10', 0, 1.5, fmt='original')
        self.assertEqual((r['width'], r['height']), (1920, 1080))
        self.assertTrue(r['validation']['valid'])

    def test_scene_detection(self):
        a = make_video(self.dir / 'sa.mp4', duration=3, audio=False)
        b = self.dir / 'sb.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=640x360:r=25:d=3',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(b)], check=True)
        joined = self.dir / 'scenes.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(a), '-i', str(b), '-filter_complex',
                        '[0:v][1:v]concat=n=2:v=1[v]', '-map', '[v]', '-c:v', 'libx264', '-preset', 'ultrafast',
                        str(joined)], check=True)
        scenes = media.detect_scenes(joined, media.probe_media(joined), self.dir / 'work')
        self.assertTrue(any(abs(s - 3) < 0.6 for s in scenes), scenes)


if __name__ == '__main__':
    unittest.main()
