"""Masterização final: grafo sem erros de mapeamento, loudness, pico, música em loop e legenda."""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import media
from app.batch import captions
from app.batch.mastering import AudioVisualMastering, MasteringError
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


if __name__ == '__main__':
    unittest.main()
