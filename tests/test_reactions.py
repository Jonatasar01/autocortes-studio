"""Reações na legenda animada: picos, som nos picos, vitória/dinheiro e vícios de fala."""
from __future__ import annotations

import subprocess
import unittest

from app import media, reactions, subtitles
from tests.helpers import HAS_FFMPEG


def w(start, text, db=-20.0):
    return {'start': start, 'end': start + 0.3, 'text': text, 'db': db}


class PeakTests(unittest.TestCase):
    def test_loud_word_is_a_peak_and_stopwords_are_not(self):
        words = [w(i * 0.5, 'palavra') for i in range(20)] + [w(10.5, 'GOOOL', -8), w(11.0, 'que', -5)]
        self.assertEqual([p['text'] for p in reactions.find_peaks(words, 30)], ['GOOOL'])

    def test_budget_and_spacing(self):
        words = [w(i, 'fala', -20) for i in range(30)] + [w(5.1, 'uau', -5), w(6.0, 'olha', -4), w(20, 'nossa', -6)]
        found = reactions.find_peaks(words, 30)   # 30 s: no máximo 2 picos, a 4 s um do outro
        self.assertEqual([p['text'] for p in found], ['olha', 'nossa'])

    def test_laugh_counts_without_audio(self):
        words = [{'start': 1.0, 'end': 1.4, 'text': 'kkkkk'}, {'start': 2.0, 'end': 2.2, 'text': 'mano'}]
        self.assertEqual([p['text'] for p in reactions.find_peaks(words, 10)], ['kkkkk'])

    def test_peak_stands_alone(self):
        lines = [[w(0, 'olha'), {**w(0.4, 'isso'), 'peak': True}, w(0.8, 'aqui')]]
        self.assertEqual([[x['text'] for x in line] for line in reactions.isolate_peaks(lines)],
                         [['olha'], ['isso'], ['aqui']])


class WordTests(unittest.TestCase):
    def test_win_words_are_emphasized(self):
        lines = [[w(0, 'eu'), w(0.4, 'GANHEI'), w(0.8, 'mil'), w(1.2, 'reais!')]]
        reactions.mark(lines, peaks=False, wins=True)
        self.assertEqual([bool(x.get('emph')) for x in lines[0]], [False, True, True, True])

    def test_fillers_leave_the_subtitle(self):
        lines = [[w(0, 'ééé'), w(0.4, 'tipo'), w(0.8, 'tipo'), w(1.2, 'é'), w(1.6, 'há'), w(2.0, 'hum')],
                 [w(3, 'aaah')]]
        out = reactions.clean_fillers(lines)
        self.assertEqual([[x['text'] for x in line] for line in out], [['tipo', 'é', 'há'], ['aaah']])
        self.assertEqual(out[0][0]['end'], 1.1)   # "tipo" herda o tempo da repetição


class RenderTests(unittest.TestCase):
    def test_peak_event_grows_and_shakes(self):
        lines = [[w(0, 'olha')], [{**w(0.5, 'gol'), 'peak': True}]]
        ass = subtitles.to_ass_dynamic(lines, 1080, 1920)
        peak = [line for line in ass.splitlines() if 'GOL' in line][0]
        self.assertIn(subtitles.PEAK_ANIM, peak)
        self.assertNotIn('\\fscx108', peak)

    def test_sound_goes_into_the_audio_graph(self):
        info = {'width': 640, 'height': 360, 'video_index': 0, 'audio_index': 1, 'duration': 6.0}
        args, _ = media.build_export_args(media.Path('x.mp4'), info, 0, 6, '9:16', 'fit', None, True, 'o.mp4',
                                          sfx=([1.0, 3.5], 'whoosh'))
        graph = args[args.index('-filter_complex') + 1]
        self.assertIn('[amain]', graph)
        self.assertIn('adelay=650:all=1', graph)
        self.assertIn('amix=inputs=3', graph)
        self.assertIn('loudnorm', graph.split('amix')[1])   # normaliza depois de misturar
        self.assertEqual(args[args.index('-map', args.index('[vout]')) + 1], '[aout]')
        self.assertNotIn('-af', args)

    @unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
    def test_sound_graph_runs_in_ffmpeg(self):
        for sound in reactions.SOUNDS:
            graph = '[0:a]anull[amain];' + reactions.sound_graph([0.5, 2.0], sound, 'amain', 'aout')
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=300:d=3', '-filter_complex', graph,
                            '-map', '[aout]', '-f', 'null', '-'], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
