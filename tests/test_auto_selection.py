"""Seleção automática: quantidade pelo conteúdo, laços do Whisper descartados e fala de partida penalizada."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from app import autopilot, curation, selection
from tests.test_merge_v23 import PHRASES, FakeProvider, openai_reply
from tests.helpers import fake_transcript


def seg(start, end, text, lp=-0.2):
    return {'start': start, 'end': end, 'text': text, 'avg_logprob': lp, 'no_speech_prob': 0.0, 'words': []}


GOOD = [
    'Gente, eu tenho uma novidade pra contar pra vocês hoje.',
    'Lembra quando eu falei que vocês seriam os primeiros a saber?',
    'Então, a gente vai casar esse ano, no mês onze.',
    'Eu fiquei muito feliz, quase chorei quando assinei os papéis.',
    'Foi a primeira vez que eu contei isso pra alguém fora da família.',
    'Mano, não acredito que eu tô falando isso ao vivo, que loucura.',
]
STORY = [
    'Deixa eu contar uma história que aconteceu comigo na época do campeonato.',
    'Um moleque de catorze anos jogou contra mim e ganhou de três a zero.',
    'Eu liguei pro dono do time e falei, contrata esse menino agora.',
    'Hoje ele ganhou um milhão de dólares na premiação mundial.',
    'E de gratidão ele falou que vai me pagar trezentos mil reais.',
    'Sério, eu fiquei sem palavras, mano, que loucura essa vida.',
]
CALLOUTS = ['Smoke no meio, bang na varanda, drone no mercado.', 'Spike no B, costa, costa, ult pronta.',
            'Matei um, morri, tem dois no bomb, defusa.', 'Rotaciona pro A, drop a vandal, flash agora.']


def build(blocks):
    """Monta uma transcrição com blocos separados por 40 s de silêncio."""
    out, t = [], 0.0
    for block in blocks:
        for line in block:
            out.append(seg(t, t + 4.5, line))
            t += 5.0
        t += 40.0
    return out, t


class LoopTests(unittest.TestCase):
    def test_loop_mask_keeps_first_occurrence(self):
        toks = 'bora bora bora bora bora vamos jogar vou agachar vou agachar vou agachar vou agachar fim'.split()
        mask = selection.loop_mask(toks)
        kept = [t for t, bad in zip(toks, mask) if not bad]
        self.assertEqual(kept, ['bora', 'vamos', 'jogar', 'vou', 'agachar', 'fim'])

    def test_short_repetition_is_not_a_loop(self):
        toks = 'não não não é isso'.split()
        self.assertFalse(any(selection.loop_mask(toks)))

    def test_collapse_loops_for_titles(self):
        text = 'Vamos lá, bora, bora, bora, bora, bora, bora. Agora sim.'
        self.assertEqual(selection.collapse_loops(text), 'Vamos lá, bora… Agora sim.')


class InterestTests(unittest.TestCase):
    def score(self, lines):
        text = ' '.join(lines)
        return selection._interest(text, selection.tokens(text))

    def test_story_and_news_score_above_game_callouts(self):
        news, news_pen, reasons = self.score(GOOD)
        game, game_pen, game_reasons = self.score(CALLOUTS)
        self.assertGreater(news, game + 8)
        self.assertEqual(news_pen, 0)
        self.assertGreater(game_pen, 5)
        self.assertIn('Notícia ou assunto pessoal', reasons)
        self.assertTrue(any('fala de jogo' in r for r in game_reasons))


class AutoSelectionTests(unittest.TestCase):
    def test_automatic_count_follows_quality(self):
        loop = ['bora, bora, bora, bora, bora, bora, bora, bora, bora, bora, bora, bora.'] * 6
        segments, duration = build([GOOD, CALLOUTS * 2, STORY, loop])
        clips, info = selection.select_clips(segments, duration, 30, selection.AUTO)
        self.assertEqual(info['count_mode'], 'automatico')
        starts = sorted(c['start'] for c in clips)
        # As duas partes boas entram; a de laço nunca; nenhum corte colado em outro.
        self.assertTrue(any(c['start'] < 30 for c in clips))
        story_start = 30 + 40 + 8 * 5 + 40 - 1
        self.assertTrue(any(story_start <= c['start'] < story_start + 31 for c in clips), starts)
        loop_start = duration - 40 - 6 * 5
        self.assertFalse(any(c['start'] >= loop_start - 1 for c in clips))
        for a, b in zip(sorted(clips, key=lambda c: c['start']), sorted(clips, key=lambda c: c['start'])[1:]):
            self.assertGreaterEqual(b['start'] - a['end'], selection.AUTO_GAP - 0.5)
        self.assertTrue(all(c['end'] - c['start'] >= selection.AUTO_MIN_SECONDS - 0.5 for c in clips))
        self.assertTrue(all('bora, bora, bora' not in c['description'] for c in clips))

    def test_fixed_count_still_respected(self):
        segments, duration = build([GOOD, STORY, GOOD, STORY])
        clips, info = selection.select_clips(segments, duration, 20, 2)
        self.assertEqual(len(clips), 2)
        self.assertNotIn('count_mode', info)

    def test_auto_cap_by_duration(self):
        self.assertEqual(selection.auto_cap(600), 3)
        self.assertEqual(selection.auto_cap(3600 * 2), 12)
        self.assertEqual(selection.auto_cap(3600 * 20), selection.AUTO_MAX)


class AutopilotTests(unittest.TestCase):
    def test_max_clips_zero_exports_every_approved_clip(self):
        clips = [{'id': i, 'rank': i, 'score': s, 'selection_mode': 'transcricao'}
                 for i, s in enumerate([92, 88, 60, 81, 40], 1)]
        cfg = {'max_clips': 0, 'min_score': 70, 'include_temporal': False}
        chosen, skipped = autopilot.choose(clips, cfg)
        self.assertEqual([c['id'] for c in chosen], [1, 2, 4])
        self.assertEqual(len(skipped), 2)
        chosen, _ = autopilot.choose(clips, {**cfg, 'max_clips': 1})
        self.assertEqual([c['id'] for c in chosen], [1])


class CurationAutoTests(unittest.TestCase):
    def test_ai_curation_accepts_automatic_count(self):
        fake = FakeProvider(openai_reply)
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-teste'}), mock.patch.object(curation, '_post', fake):
            clips, status = curation.curate('openai', fake_transcript(25, PHRASES), 25, 12, selection.AUTO, [])
        self.assertEqual(status['state'], 'ok')
        self.assertGreaterEqual(len(clips), 1)
        self.assertTrue(all(c['end'] - c['start'] >= selection.AUTO_MIN_SECONDS - 0.5 for c in clips))


if __name__ == '__main__':
    unittest.main()
