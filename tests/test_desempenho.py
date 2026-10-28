"""Fase 3 (desempenho): cache das faixas pretas, exportação pela placa com volta para a CPU, um só modelo Whisper
na GPU e importação de vídeo do disco sem cópia."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, media, service, storage, transcription
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import OWNED, ServiceBase

INFO = {'width': 1920, 'height': 1080, 'duration': 600.0, 'video_index': 0, 'fps': 30, 'audio_index': None}


class SourceReadLimitTests(unittest.TestCase):
    """Pausas cortadas deixam a saída mais curta que o trecho: a leitura da origem precisa parar no fim do corte."""

    def test_input_is_limited_to_the_clip(self):
        args, _ = media.build_export_args(Path('v.mp4'), {**INFO, 'audio_index': 1}, 120.0, 30.0, '16:9', 'fit',
                                          None, False, 'out.mp4', keep=[(0.0, 10.0), (14.0, 30.0)])
        i = args.index('-i')
        self.assertEqual(args[:i], ['-ss', '120.000', '-t', '30.000'])

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_shortened_clip_stops_reading_at_the_clip_end(self):
        tmp = Path(tempfile.mkdtemp(prefix='keep-'))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        video = make_video(tmp / 'longo.mp4', size='320x180', duration=40, fps=25)
        info = media.probe_media(video)
        frames = []
        real = media.run_ffmpeg

        def spy(args, *a, **kw):
            out = real(args, *a, **kw)
            frames.append(args)
            return out
        with mock.patch.object(media, 'run_ffmpeg', side_effect=spy):
            out = media.export_clip(video, info, tmp / 'out', 'corte', 5.0, 15.0, fmt='16:9', framing='fit',
                                    keep=[(0.0, 4.0), (6.0, 10.0)])
        render = next(a for a in frames if '-filter_complex' in a)
        self.assertLess(render.index('-t'), render.index('-i'))
        self.assertAlmostEqual(media.probe_media(tmp / 'out' / Path(out['file']).name)['duration'], 8.0, delta=0.3)


class LetterboxCacheTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='lb-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.src = self.dir / 'v.mp4'
        self.src.write_bytes(b'x' * 1000)

    def test_whole_video_readings_are_done_once(self):
        calls = []

        def fake(source, at, cancel):
            calls.append(at)
            return (1920, 800, 0, 140)        # barras pretas em cima e embaixo
        with mock.patch.object(media, '_cropdetect', side_effect=fake):
            a = media.detect_letterbox(self.src, INFO, 100, 160, cache_dir=self.dir / 'cache')
            first = len(calls)
            b = media.detect_letterbox(self.src, INFO, 300, 340, cache_dir=self.dir / 'cache')
        self.assertEqual(first, 12)
        self.assertEqual(len(calls) - first, 6)      # só as do trecho novo
        self.assertEqual(a, b)
        self.assertEqual((a['h'], a['y']), (800, 140))

    def test_cache_is_dropped_when_the_file_changes(self):
        calls = []
        with mock.patch.object(media, '_cropdetect', side_effect=lambda *a: calls.append(1) or None):
            media.detect_letterbox(self.src, INFO, 0, 30, cache_dir=self.dir / 'cache')
            self.src.write_bytes(b'y' * 2000)
            media.detect_letterbox(self.src, INFO, 0, 30, cache_dir=self.dir / 'cache')
        self.assertEqual(len(calls), 24)


class EncoderTests(unittest.TestCase):
    def test_choice(self):
        with mock.patch.object(config, 'ENCODER', 'x264'):
            self.assertEqual(media.choose_encoder(), 'x264')
        with mock.patch.object(config, 'ENCODER', 'auto'), mock.patch.object(media, 'nvenc_available', return_value=False):
            self.assertEqual(media.choose_encoder(), 'x264')
        with mock.patch.object(config, 'ENCODER', 'auto'), mock.patch.object(media, 'nvenc_available', return_value=True):
            self.assertEqual(media.choose_encoder(), 'nvenc')

    def test_nvenc_quality_follows_the_frame_rate(self):
        args30 = media.video_codec_args('nvenc', False, 30)
        args60 = media.video_codec_args('nvenc', False, 60)
        self.assertEqual(args30[args30.index('-cq') + 1], str(media.NVENC_CQ_30FPS))
        self.assertEqual(args60[args60.index('-cq') + 1], str(media.NVENC_CQ_60FPS))
        self.assertIn('libx264', media.video_codec_args('x264', False, 30))

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_nvenc_failure_falls_back_to_x264(self):
        d = Path(tempfile.mkdtemp(prefix='enc-'))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        src = make_video(d / 'v.mp4', size='640x360', duration=3)
        info = media.probe_media(src)
        real = media.run_ffmpeg

        def no_gpu(args, **kw):
            if 'h264_nvenc' in args:
                raise media.FFmpegError('sem sessão NVENC livre', 'OpenEncodeSessionEx failed')
            return real(args, **kw)
        with mock.patch.object(media, 'choose_encoder', return_value='nvenc'), \
                mock.patch.object(media, 'run_ffmpeg', side_effect=no_gpu):
            result = media.export_clip(src, info, d / 'out', 'c', 0, 2, fmt='9:16', framing='blur',
                                       ass_text=None, srt_text=None, normalize_audio=False)
        self.assertEqual(result['encoder'], 'x264')
        self.assertTrue(result['validation']['valid'])


class SharedWhisperTests(unittest.TestCase):
    def test_analysis_and_subtitles_share_one_model_on_the_gpu(self):
        built = []

        class FakeModel:
            def __init__(self, name, device, compute_type):
                built.append((name, device, compute_type))
        fake_module = mock.Mock(WhisperModel=FakeModel)
        with mock.patch.dict('sys.modules', {'faster_whisper': fake_module}), \
                mock.patch.object(transcription, '_cuda_dll_dirs'), \
                mock.patch.object(config, 'WHISPER_MODEL', 'auto'), \
                mock.patch.object(config, 'WHISPER_REFINE_MODEL', transcription.GPU_MODEL), \
                mock.patch.dict(transcription._cache, clear=True), \
                mock.patch.dict(transcription._refine_cache, clear=True):
            a = transcription._load_model('cuda')
            b = transcription._load_refine_model()
            # Na ordem inversa também (legenda primeiro, depois uma análise).
            transcription._cache.clear()
            transcription._refine_cache.clear()
            c = transcription._load_refine_model()
            d = transcription._load_model('cuda')
        self.assertIs(a, b)
        self.assertIs(c, d)
        self.assertEqual(len(built), 2)


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class ImportFromPathTests(ServiceBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.video = make_video(cls.tmp / 'live no disco.mp4', size='640x360', duration=20, fps=25)

    def test_same_disk_becomes_a_hard_link(self):
        info = service.check_path(str(self.video))
        self.assertEqual(info['size_bytes'], self.video.stat().st_size)
        self.assertTrue(info['no_copy'])                     # mesmo volume da pasta de dados
        res = service.create_from_path(str(self.video), {**OWNED, 'editing_mode': 'manual'})
        p = storage.read_project(res['id'])
        source = storage.project_dir(res['id']) / p['filename']
        self.assertTrue(os.path.samefile(source, self.video))   # o mesmo arquivo, sem cópia
        self.assertEqual(p['source_link'], 'link')
        self.assertTrue(p['quick_fingerprint'].startswith('quick:'))
        # O vídeo já está num projeto: a verificação seguinte avisa.
        self.assertEqual(service.check_path(str(self.video))['duplicate']['id'], res['id'])
        # Apagar o projeto não apaga o original.
        service.delete_project(res['id'])
        self.assertTrue(self.video.is_file())

    def test_other_disk_is_copied(self):
        with mock.patch.object(os, 'link', side_effect=OSError(18, 'outro disco')):
            res = service.create_from_path(str(self.video), {**OWNED, 'editing_mode': 'manual'})
        p = storage.read_project(res['id'])
        self.assertEqual(p['source_link'], 'copia')
        self.assertFalse(os.path.samefile(storage.project_dir(res['id']) / p['filename'], self.video))

    def test_bad_paths(self):
        for path, code in (('relativo.mp4', 'bad_path'), (str(self.tmp / 'nao-existe.mp4'), 'not_found'),
                           (str(self.tmp / 'texto.txt'), 'bad_extension')):
            with self.assertRaises(AppError) as ctx:
                service.check_path(path)
            self.assertEqual(ctx.exception.code, code)


if __name__ == '__main__':
    unittest.main()
