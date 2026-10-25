"""Masterização final: grafo sem erros de mapeamento, loudness, pico, música em loop e legenda."""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import media
from app.batch import captions
from app.batch.mastering import AudioVisualMastering, MasteringError, MasteringSettings
from tests.helpers import HAS_FFMPEG, fake_transcript, make_video


class GraphTests(unittest.TestCase):
    def test_graph_order_and_labels(self):
        m = AudioVisualMastering()
        args = m.build_args(Path('in.mp4'), Path('bgm.mp3'), 'legenda.ass', 'out.mp4')
        graph = args[args.index('-filter_complex') + 1]
        video, voice, music, mix = graph.split(';')
        self.assertTrue(video.startswith('[0:v:0]scale=1080:1920') and video.endswith('[vout]'))
        self.assertLess(video.index('crop=1080:1920'), video.index('ass=legenda.ass'))
        self.assertTrue(voice.startswith('[0:a:0]acompressor='))
        self.assertLess(voice.index('acompressor'), voice.index('loudnorm=I=-14:LRA=11:TP=-1'))
        self.assertTrue(voice.endswith('[a1]'))
        self.assertTrue(music.startswith('[1:a:0]volume=0.05') and music.endswith('[a2]'))
        self.assertIn('[a1][a2]amix=inputs=2:duration=first:dropout_transition=2', mix)
        self.assertIn('level=0', mix)
        self.assertTrue(mix.endswith('[aout]'))
        self.assertEqual(args[args.index('-stream_loop') + 1], '-1')
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == '-map'], ['[vout]', '[aout]'])

    def test_without_music(self):
        args = AudioVisualMastering().build_args(Path('in.mp4'), None, None, 'out.mp4')
        graph = args[args.index('-filter_complex') + 1]
        self.assertNotIn('[1:a', graph)
        self.assertNotIn('ass=', graph)
        self.assertEqual(args.count('-i'), 1)

    def test_measured_loudnorm_is_linear(self):
        measured = {'input_i': '-23.1', 'input_lra': '5.0', 'input_tp': '-6.0', 'input_thresh': '-33.5',
                    'target_offset': '0.2'}
        chain = AudioVisualMastering().audio_chain(False, measured)
        self.assertIn('measured_I=-23.1', chain)
        self.assertIn('linear=true', chain)



class NarratedGraphTests(unittest.TestCase):
    def graph(self, args):
        return args[args.index('-filter_complex') + 1]

    def test_three_tracks_voice_first(self):
        args = AudioVisualMastering().build_args(Path('in.mp4'), Path('bgm.mp3'), 'legenda.ass', 'out.mp4',
                                                 tts_in=Path('voz.mp3'), duration=12.5)
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == '-i'], ['in.mp4', 'voz.mp3', 'bgm.mp3'])
        video, voice, original, music, mix = self.graph(args).split(';')
        self.assertTrue(video.endswith('[vout]'))
        self.assertNotIn('tpad', video)
        self.assertTrue(voice.startswith('[1:a:0]acompressor='))
        self.assertLess(voice.index('acompressor'), voice.index('loudnorm=I=-14:LRA=11:TP=-1'))
        self.assertTrue(voice.endswith('[nvoz]'))
        self.assertTrue(original.startswith('[0:a:0]') and 'volume=0.08' in original)
        self.assertNotIn('loudnorm', original)
        self.assertTrue(music.startswith('[2:a:0]volume=0.05'))
        self.assertIn('[nvoz][norig][nbgm]amix=inputs=3:duration=first', mix)
        self.assertIn('normalize=0', mix)
        self.assertTrue(mix.endswith('[aout]'))
        # A duração final é a da narração; sem -shortest (o vídeo original não manda mais).
        self.assertEqual(args[args.index('-t') + 1], '12.500')
        self.assertNotIn('-shortest', args)
        self.assertEqual(args[args.index('-stream_loop') + 1], '-1')
        self.assertLess(args.index('-stream_loop'), args.index('bgm.mp3'))
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == '-map'], ['[vout]', '[aout]'])

    def test_without_music_or_original_audio(self):
        m = AudioVisualMastering()
        no_bgm = self.graph(m.build_args(Path('in.mp4'), None, None, 'o.mp4', tts_in=Path('v.mp3'), duration=5))
        self.assertIn('[nvoz][norig]amix=inputs=2', no_bgm)
        self.assertNotIn('[2:a', no_bgm)
        voice_only = self.graph(m.build_args(Path('in.mp4'), None, None, 'o.mp4', tts_in=Path('v.mp3'),
                                             duration=5, original_audio=False))
        self.assertNotIn('amix', voice_only)
        self.assertNotIn('[0:a', voice_only)
        self.assertTrue(voice_only.endswith('level=0[aout]'))

    def test_short_video_freezes_last_frame_before_subtitles(self):
        video = AudioVisualMastering().video_chain('legenda.ass', pad=1.25)
        self.assertIn('tpad=stop_mode=clone:stop_duration=1.250', video)
        self.assertLess(video.index('tpad'), video.index('ass=legenda.ass'))

    def test_custom_volume_and_required_duration(self):
        chain = AudioVisualMastering(MasteringSettings(original_volume=0.15)).narrated_audio_chain(
            '[1:a:0]', '[0:a:0]', None)
        self.assertIn('volume=0.15', chain)
        with self.assertRaises(ValueError):
            AudioVisualMastering().build_args(Path('in.mp4'), None, None, 'o.mp4', tts_in=Path('v.mp3'))

def loudness(path: Path) -> tuple[float, float]:
    err = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(path), '-map', '0:a', '-af', 'ebur128=peak=true',
                          '-f', 'null', '-'], capture_output=True, text=True).stderr
    return float(re.findall(r'I:\s+(-?[\d.]+) LUFS', err)[-1]), float(re.findall(r'Peak:\s+(-?[\d.]+) dBFS', err)[-1])


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="master '"))  # apóstrofo e espaço no caminho
        cls.video = make_video(cls.dir / 'corte.mp4', size='1280x720', duration=8)
        cls.bgm = cls.dir / 'musica.mp3'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=330:d=3',
                        '-c:a', 'libmp3lame', str(cls.bgm)], check=True)
        cls.ass = cls.dir / 'legenda, final.ass'
        cls.ass.write_text(captions.build_ass(fake_transcript(8), 8), encoding='utf-8')

    def test_render_with_music_and_subtitles(self):
        out = AudioVisualMastering().render_final_clip(self.video, self.bgm, self.ass, self.dir / 'saida' / 'final.mp4')
        info = media.probe_media(out)
        self.assertEqual((info['width'], info['height']), (1080, 1920))
        # Música de 3 s repetida; a duração segue a do vídeo (duration=first).
        self.assertAlmostEqual(info['duration'], 8, delta=0.2)
        lufs, peak = loudness(out)
        self.assertAlmostEqual(lufs, -14, delta=1.5)
        self.assertLessEqual(peak, -0.8)

    def test_missing_audio_and_files_raise_clear_errors(self):
        silent = make_video(self.dir / 'mudo.mp4', duration=3, audio=False)
        with self.assertRaisesRegex(MasteringError, 'trilha de áudio'):
            AudioVisualMastering().render_final_clip(silent, None, None, self.dir / 'x.mp4')
        with self.assertRaisesRegex(MasteringError, 'não encontrado'):
            AudioVisualMastering().render_final_clip(self.video, self.dir / 'nao.mp3', None, self.dir / 'x.mp4')



def make_voice(path: Path, duration: float) -> Path:
    """Sinal com envelope de fala (sílabas ~4 Hz), baixo (-30 dBFS), em MP3 mono 24 kHz como o edge-tts."""
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
                    f"aevalsrc='0.05*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*4*t))':s=24000:d={duration}",
                    '-c:a', 'libmp3lame', '-ac', '1', str(path)], check=True)
    return path


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class NarratedRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="narrado '"))
        cls.video = make_video(cls.dir / 'corte.mp4', size='1280x720', duration=8)
        cls.voice = make_voice(cls.dir / 'narracao.mp3', 5)
        cls.bgm = cls.dir / 'musica.mp3'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=330:d=2',
                        '-c:a', 'libmp3lame', str(cls.bgm)], check=True)
        cls.ass = cls.dir / 'legenda.ass'
        cls.ass.write_text(captions.build_ass(fake_transcript(5), 5), encoding='utf-8')

    def test_duration_follows_narration_and_voice_is_mastered(self):
        out = AudioVisualMastering().render_final_clip(self.video, self.bgm, self.ass, self.dir / 'final.mp4',
                                                       tts_audio_path=self.voice)
        info = media.probe_media(out)
        self.assertEqual((info['width'], info['height']), (1080, 1920))
        self.assertAlmostEqual(info['duration'], 5, delta=0.2)  # vídeo de 8 s cortado na narração
        lufs, peak = loudness(out)
        self.assertAlmostEqual(lufs, -14, delta=1.5)
        self.assertLessEqual(peak, -0.8)

    def test_original_audio_stays_far_below_voice(self):
        # Com a narração muda, o que sobra é o original a 0,08: ~22 dB abaixo do nível dele.
        mute = self.dir / 'muda.mp3'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono',
                        '-t', '4', '-c:a', 'libmp3lame', str(mute)], check=True)
        out = AudioVisualMastering().render_final_clip(self.video, None, None, self.dir / 'so_original.mp4',
                                                       tts_audio_path=mute)
        original, _ = loudness(self.video)
        bed, _ = loudness(out)
        self.assertLess(bed, original - 18)

    def test_short_video_is_extended_to_narration(self):
        short = make_video(self.dir / 'curto.mp4', duration=3)
        out = AudioVisualMastering().render_final_clip(short, None, None, self.dir / 'estendido.mp4',
                                                       tts_audio_path=self.voice)
        self.assertAlmostEqual(media.probe_media(out)['duration'], 5, delta=0.2)

    def test_video_without_audio_is_fine_when_narrated(self):
        silent = make_video(self.dir / 'mudo.mp4', duration=6, audio=False)
        out = AudioVisualMastering().render_final_clip(silent, None, None, self.dir / 'mudo_narrado.mp4',
                                                       tts_audio_path=self.voice)
        self.assertIsNotNone(media.probe_media(out).get('audio_index'))

    def test_missing_narration_raises(self):
        with self.assertRaisesRegex(MasteringError, 'narração não encontrado'):
            AudioVisualMastering().render_final_clip(self.video, None, None, self.dir / 'x.mp4',
                                                     tts_audio_path=self.dir / 'nao.mp3')

if __name__ == '__main__':
    unittest.main()
