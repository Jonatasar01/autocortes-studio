"""Camada de áudio: energia por FFmpeg (com cache), eventos com evidência e reforço na seleção."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import audio_events, selection
from tests.helpers import HAS_FFMPEG

HOP = audio_events.HOP


def track_from(levels: list[tuple[float, float]]) -> dict:
    """Trilha de energia sintética: ``[(duração_s, dB), ...]``."""
    db = []
    for dur, level in levels:
        db += [level] * round(dur / HOP)
    return {'hop': HOP, 'db': db, 'count': len(db)}


def words(start: float, text: str, step: float = 0.35) -> dict:
    ws = [{'start': round(start + k * step, 2), 'end': round(start + (k + 1) * step, 2), 'word': w}
          for k, w in enumerate(text.split())]
    return {'start': start, 'end': ws[-1]['end'], 'text': text, 'avg_logprob': -0.2, 'no_speech_prob': 0.0,
            'words': ws}


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class EnergyTrackTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='audio-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        # 20 s: conversa baixa, silêncio de 5 a 8 s e um pico forte de 12 a 13 s.
        self.wav = self.dir / 'teste.wav'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=20',
                        '-af', "volume='if(between(t,5,8),0,if(between(t,12,13),1,0.05))':eval=frame",
                        '-ac', '1', '-ar', '16000', str(self.wav)], check=True)
        self.info = {'audio_index': 0, 'duration': 20.0}

    def test_levels_events_and_cache(self):
        cache = self.dir / 'cache'
        track = audio_events.energy_track(self.wav, self.info, cache)
        self.assertAlmostEqual(track['count'] * HOP, 20.0, delta=0.5)
        db = track['db']
        self.assertLess(max(db[round(5.5 / HOP):round(7.5 / HOP)]), -60)          # silêncio
        self.assertGreater(min(db[round(12.2 / HOP):round(12.8 / HOP)]) - db[round(2 / HOP)], 20)  # pico
        kinds = {e['tipo'] for e in audio_events.analyze(track, [])['events']}
        self.assertTrue({'silencio', 'pico_de_volume'} <= kinds, kinds)
        for e in audio_events.analyze(track, [])['events']:
            self.assertTrue({'start', 'end', 'tipo', 'valor', 'confianca', 'origem'} <= set(e))
        # Segunda vez: do cache (sem FFmpeg).
        with mock.patch.object(audio_events.media, 'run_ffmpeg', side_effect=AssertionError('não deveria decodificar')):
            again = audio_events.energy_track(self.wav, self.info, cache)
        self.assertEqual(len(again['db']), len(db))
        # Arquivo mudou: o cache não vale mais.
        stale = json.loads((cache / f'audio_energia_v{audio_events.VERSION}.json').read_text(encoding='utf-8'))
        stale['stamp'] = [0, 0]
        (cache / f'audio_energia_v{audio_events.VERSION}.json').write_text(json.dumps(stale), encoding='utf-8')
        self.assertEqual(len(audio_events.energy_track(self.wav, self.info, cache)['db']), len(db))

    def test_video_without_audio(self):
        self.assertIsNone(audio_events.energy_track(self.wav, {'audio_index': None, 'duration': 20}, self.dir))


class EventTests(unittest.TestCase):
    def test_loud_sound_without_words_after_speech_is_a_possible_reaction(self):
        track = track_from([(60, -30), (1.5, -20), (60, -30)])
        speech = [words(55.5, 'e aí ela caiu da cadeira na frente de todo mundo')]   # termina antes do som
        events = audio_events.analyze(track, speech)['events']
        reaction = [e for e in events if e['tipo'] == 'possivel_risada']
        self.assertTrue(reaction, events)
        self.assertAlmostEqual(reaction[0]['start'], 60.0, delta=0.5)
        # O mesmo som coberto por palavras transcritas é fala alta, não reação.
        covered = [words(59.6, 'gente gente gente gente gente gente')]
        self.assertFalse([e for e in audio_events.analyze(track, covered)['events'] if e['tipo'] == 'possivel_risada'])

    def test_clipping_and_silence_are_flagged(self):
        track = track_from([(30, -30), (2, -1), (10, -70), (30, -30)])
        kinds = {e['tipo'] for e in audio_events.analyze(track, [])['events']}
        self.assertTrue({'audio_estourado', 'silencio'} <= kinds, kinds)

    def test_without_track(self):
        self.assertEqual(audio_events.analyze(None, []), {'events': [], 'index': None})


class AdjustmentTests(unittest.TestCase):
    def index(self, levels, speech):
        return audio_events.analyze(track_from(levels), speech)['index']

    def test_reaction_at_the_end_reinforces_only_with_content(self):
        idx = self.index([(60, -30), (1.5, -18), (60, -30)], [words(55.0, 'e aí ela caiu da cadeira na frente de todos')])
        bonus, evidence, _ = audio_events.window_adjustment(idx, 30, 62, has_content=True)
        self.assertEqual(bonus, audio_events.REACTION_BONUS)
        self.assertEqual(evidence[0]['origem'], 'audio')
        self.assertEqual(audio_events.window_adjustment(idx, 30, 62, has_content=False)[0], 0)   # sozinho não decide
        self.assertEqual(audio_events.window_adjustment(idx, 0, 40, has_content=True)[0], 0)     # sem reação no trecho

    def test_clipped_audio_is_a_warning_even_without_content(self):
        idx = self.index([(30, -30), (2, -1), (30, -30)], [])
        penalty, _, warnings = audio_events.window_adjustment(idx, 20, 40, has_content=False)
        self.assertLess(penalty, 0)
        self.assertTrue(any('estourado' in w for w in warnings))

    def test_selection_prefers_the_story_that_ends_in_a_reaction(self):
        story = ['Gente, deixa eu contar o que aconteceu no casamento da minha irmã.',
                 'O noivo esqueceu a aliança em casa e voltou correndo de moto.',
                 'Quando ele chegou, a igreja inteira aplaudiu o noivo suado.',
                 'E a minha irmã só olhou pra ele e disse: atrasado, como sempre.']
        segs, t = [], 0.0
        for block_start in (0.0, 200.0):
            t = block_start
            for line in story:
                s = words(t, line)
                segs.append(s)
                t = s['end'] + 0.5
        end_second = t
        # Mesmo texto duas vezes; só a segunda história termina com reação sonora (riso sem palavras).
        levels = [(end_second + 0.2, -30), (2.0, -18), (300 - end_second - 2.2, -30)]
        idx = audio_events.analyze(track_from(levels), segs)['index']
        with mock.patch.object(selection.config, 'SELECTION_ENGINE', 'v1'):
            clips, _ = selection.select_clips(segs, 300, 20, 0, [], audio=idx)
        best = clips[0]
        self.assertGreater(best['start'], 150, clips)
        self.assertTrue(any(e['origem'] == 'audio' for e in best['evidence']), best['evidence'])
        self.assertGreaterEqual(best['end'], end_second + 2.0)   # a risada depois da última palavra entra no corte


class PipelineTests(unittest.TestCase):
    def test_audio_failure_does_not_break_the_analysis(self):
        from app import pipeline

        class Ctx:
            project_id = 'x'
            cancel_event = None

            def stage(self, *a):
                pass

            def warn(self, message):
                self.warned = message

        ctx = Ctx()
        with mock.patch.object(audio_events, 'energy_track', side_effect=RuntimeError('ffmpeg sumiu')):
            out = pipeline._audio_layer(ctx, Path('.'), {'audio_index': 0, 'duration': 10}, [words(0, 'oi tudo bem')],
                                        original=Path('x.mp4'))
        self.assertIsNone(out)
        self.assertIn('só a transcrição', ctx.warned)

    def test_layer_can_be_turned_off(self):
        from app import pipeline
        with mock.patch.object(pipeline.config, 'AUDIO_LAYER', False):
            self.assertIsNone(pipeline._audio_layer(None, Path('.'), {'audio_index': 0}, [words(0, 'oi')],
                                                    original=Path('x.mp4')))


if __name__ == '__main__':
    unittest.main()
