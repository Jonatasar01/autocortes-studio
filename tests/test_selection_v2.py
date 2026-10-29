"""Motor de seleção v2: nota editorial × técnica, contexto (pergunta antes, reação depois), quantidade automática
relativa ao vídeo e fusão com a curadoria por IA. Transcrições sintéticas, sem vídeo."""
from __future__ import annotations

import unittest

from app import config, selection

_ENGINE = config.SELECTION_ENGINE


def setUpModule():
    config.SELECTION_ENGINE = 'v2'   # o v2 é opcional (AUTOCORTES_SELECTION_ENGINE=v2); estes testes são dele


def tearDownModule():
    config.SELECTION_ENGINE = _ENGINE


def seg(start: float, text: str, dur: float | None = None, lp: float = -0.2) -> dict:
    """Segmento com tempo por palavra; ~0,35 s por palavra."""
    words = text.split()
    dur = dur or max(0.6, 0.35 * len(words))
    step = dur / len(words)
    return {'start': start, 'end': start + dur, 'text': text, 'avg_logprob': lp, 'no_speech_prob': 0.0,
            'words': [{'start': round(start + k * step, 2), 'end': round(start + (k + 1) * step, 2), 'word': w}
                      for k, w in enumerate(words)]}


def talk(start: float, lines: list[str], gap: float = 0.4) -> list[dict]:
    out, t = [], start
    for line in lines:
        s = seg(t, line)
        out.append(s)
        t = s['end'] + gap
    return out


# Enrolação de live: frases sem assunto comum entre si (não se repetem, como no ao vivo).
FILLER = ['Beleza, vamos ver aqui o que tem pra hoje.', 'Deixa eu ajeitar a câmera rapidinho.',
          'Tá todo mundo me ouvindo bem agora?', 'Certo, então bora continuar.',
          'Olha o chat mandando mensagem aqui.', 'Peraí que eu vou pegar uma água.',
          'Quem chegou agora, seja bem-vindo.', 'Esse microfone tá meio baixo, né?',
          'Calma que já já a gente começa.', 'Valeu pelo follow, galera.',
          'Hoje tá calor demais aqui no quarto.', 'Deixa eu ver uma coisa no celular.',
          'Alguém sabe que horas termina o jogo?', 'Bebi café demais hoje cedo.',
          'Minha internet caiu ontem de noite.', 'Já volto, só um minutinho.']

STORY = ['Gente, eu preciso contar uma história do meu casamento com a Ana.',
         'No dia do casamento a Ana chegou atrasada duas horas na igreja.',
         'O padre já queria ir embora e a família da Ana inteira ligando pra ela.',
         'Aí descobri que a Ana tinha ficado presa no elevador do salão.',
         'Até hoje a família lembra do casamento por causa do elevador.',
         'Foi o dia mais louco da minha vida, sério mesmo.']


def filler(start: float, n: int) -> list[dict]:
    return talk(start, [FILLER[k % len(FILLER)] for k in range(n)], gap=0.8)


def select(segments, duration, **kw):
    return selection.select_clips(segments, duration, kw.pop('target', 35), kw.pop('count', 0), [], **kw)


class ScoreTests(unittest.TestCase):
    def test_scores_are_split_and_explained(self):
        segs = talk(0, STORY)
        units = selection.build_units(segs)
        total, b, reasons = selection.score_window(units, 0, len(units) - 1, 35, [])
        self.assertTrue(0 <= total <= 100)
        for key in ('editorial', 'tecnica', 'assunto', 'contexto', 'interesse', 'clareza', 'inicio_fim'):
            self.assertIn(key, b)
        self.assertTrue(any('assunto' in r.lower() for r in reasons), reasons)

    def test_reaction_burst_ranks_below_a_story(self):
        burst = talk(0, ['Porra, caralho, mentira!', 'Nossa, que absurdo, mano!', 'Caralho, porra, sério!',
                         'Mentira, que loucura!', 'Porra, que isso, mano!', 'Absurdo, caralho!',
                         'Mentira, nossa senhora!', 'Porra, mano, sério!', 'Caralho, que doideira!',
                         'Nossa, ridículo isso!', 'Porra, absurdo, mano!', 'Mentira, caralho, sério!'])
        story = talk(0, STORY)
        sb = selection.score_window(selection.build_units(burst), 0, len(burst) - 1, 35, [])
        ss = selection.score_window(selection.build_units(story), 0, len(story) - 1, 35, [])
        self.assertGreater(ss[0], sb[0])
        self.assertTrue(any('reação' in r.lower() for r in sb[2]), sb[2])

    def test_pauses_while_reading_chat_are_tolerated(self):
        tight = talk(0, STORY, gap=0.3)
        loose = talk(0, STORY, gap=3.0)    # um minuto de história com pausas para o chat
        a = selection.score_window(selection.build_units(tight), 0, len(tight) - 1, 35, [])[0]
        b = selection.score_window(selection.build_units(loose), 0, len(loose) - 1, 35, [])[0]
        self.assertLess(a - b, 15)


class ContextTests(unittest.TestCase):
    def test_answer_keeps_its_question(self):
        segs = filler(0, 6)
        q = seg(segs[-1]['end'] + 1.0, 'Você já pensou em casar com a Ana esse ano?')
        answer = talk(q['end'] + 0.5, ['Pensei sim, o casamento com a Ana vai ser em dezembro.',
                                       'A família da Ana já reservou a igreja e o salão do casamento.',
                                       'Eu nunca contei isso pra ninguém, mas o casamento já tá pago.',
                                       'Agora é só esperar dezembro e torcer pra Ana não desistir.'])
        segs += [q] + answer + filler(answer[-1]['end'] + 2, 6)
        clips, _ = select(segs, segs[-1]['end'] + 1, target=20)
        best = clips[0]
        self.assertLessEqual(best['start'], q['start'] + 0.05, best)

    def test_late_reaction_is_kept(self):
        segs = talk(0, STORY[:5])
        reaction = seg(segs[-1]['end'] + 0.6, 'Kkkkkk mentira!', dur=1.2)
        segs += [reaction] + filler(reaction['end'] + 2.5, 8)
        clips, _ = select(segs, segs[-1]['end'] + 1, target=20)
        story_clip = next(c for c in clips if c['start'] < 2)
        self.assertGreaterEqual(story_clip['end'], reaction['end'] - 0.05, story_clip)

    def test_does_not_end_mid_sentence(self):
        segs = talk(0, STORY[:5] + ['E aí no final a Ana falou que'])
        segs += filler(segs[-1]['end'] + 3, 6)
        clips, _ = select(segs, segs[-1]['end'] + 1, target=20)
        first = next(c for c in clips if c['start'] < 2)
        self.assertFalse(first['description'].rstrip().endswith('que'), first['description'])

    def test_two_close_stories_become_two_clips(self):
        story2 = ['Outra coisa, eu fui assaltado na padaria do bairro ontem.',
                  'O ladrão da padaria levou só o pão e deixou meu celular.',
                  'A dona da padaria correu atrás do ladrão com uma vassoura.',
                  'No fim o ladrão devolveu o pão e pediu desculpa pra padaria.',
                  'Juro, a padaria inteira aplaudiu a dona da vassoura.']
        segs = talk(0, STORY)
        segs += filler(segs[-1]['end'] + 2, 3)
        segs += talk(segs[-1]['end'] + 2, story2)
        segs += filler(segs[-1]['end'] + 2, 3)
        clips, _ = select(segs, segs[-1]['end'] + 1, target=20)
        texts = [c['description'] for c in clips[:3]]
        self.assertTrue(any('casamento' in t for t in texts) and any('padaria' in t for t in texts), texts)


class QuantityAndFieldsTests(unittest.TestCase):
    def test_automatic_count_is_relative_to_video_length(self):
        segs = []
        t = 0.0
        for _ in range(40):     # 40 histórias iguais em estrutura, ~2 h de "live"
            block = talk(t, STORY)
            segs += block + filler(block[-1]['end'] + 2, 2)
            t = segs[-1]['end'] + 120
        clips, info = select(segs, t, target=30)
        hours = t / 3600
        self.assertLessEqual(len(clips), max(3, round(selection.AUTO_CLIPS_PER_HOUR * hours)) + 1)
        self.assertEqual(info['count_mode'], 'automatico')

    def test_clip_fields_and_valid_times(self):
        segs = talk(0, STORY) + filler(30, 6)
        duration = segs[-1]['end'] + 1
        clips, _ = select(segs, duration, target=20)
        for c in clips:
            self.assertTrue(0 <= c['start'] < c['end'] <= duration)
            for key in ('editorial_score', 'technical_score', 'confidence', 'evidence', 'warnings', 'sources'):
                self.assertIn(key, c)
            self.assertEqual(c['sources'], ['heuristica'])

    def test_without_transcript_falls_back_to_time(self):
        clips, info = select([], 600, target=30)
        self.assertEqual(info['mode'], 'temporal')
        self.assertTrue(clips)

    def test_default_engine_is_the_validated_one(self):
        self.assertEqual(_ENGINE, 'v1')

    def test_previous_engine_still_available(self):
        segs = talk(0, STORY) + filler(30, 6)
        units = selection.build_units(segs)
        old = selection.score_window_v1(units, 0, len(units) - 1, 35, [])
        self.assertIn('inicio_fim', old[1])
        self.assertNotIn('editorial', old[1])


class MergeTests(unittest.TestCase):
    def clip(self, start, end, score, title):
        return {'start': start, 'end': end, 'score': score, 'title': title, 'description': title,
                'reasons': [], 'selection_mode': 'transcricao'}

    def test_strong_heuristic_clip_survives_the_ai_list(self):
        ai = [self.clip(100, 160, 82, 'IA A'), self.clip(400, 450, 75, 'IA B')]
        heur = [self.clip(1000, 1060, 90, 'Heurística forte'), self.clip(102, 158, 70, 'Mesma da IA A')]
        merged = selection.merge_candidates(ai, heur)
        titles = [c['title'] for c in merged]
        self.assertIn('Heurística forte', titles)
        self.assertNotIn('Mesma da IA A', titles)           # sobreposta: vira uma só
        a = next(c for c in merged if c['title'] == 'IA A')
        self.assertEqual(sorted(a['sources']), ['heuristica', 'ia'])
        self.assertEqual([c['rank'] for c in merged], list(range(1, len(merged) + 1)))

    def test_unlimited_merge_keeps_everything(self):
        heur = [self.clip(10000 + 200 * k, 10060 + 200 * k, 95, f'H{k}') for k in range(30)]
        ai = [self.clip(200 * k, 60 + 200 * k, 80, f'IA{k}') for k in range(25)]
        self.assertEqual(len(selection.merge_candidates(ai, heur, limit=None)), 55)

    def test_high_heuristic_scores_do_not_crowd_out_the_ai_under_the_limit(self):
        # Caso real (live de 5 h, limite 20): heurística 95–100, IA 70–85. Ordenado pela nota, só 3 da IA
        # entravam; intercalado, metade das vagas é da IA, cada lista na sua ordem.
        heur = [self.clip(10000 + 200 * k, 10060 + 200 * k, 100 - k * 0.2, f'H{k}') for k in range(20)]
        ai = [self.clip(200 * k, 60 + 200 * k, 85 - k, f'IA{k}') for k in range(15)]
        merged = selection.merge_candidates(ai, heur, limit=20)
        self.assertEqual(len(merged), 20)
        self.assertEqual(sum('ia' in c['sources'] for c in merged), 10)
        self.assertEqual([c['title'] for c in merged[:4]], ['IA0', 'H0', 'IA1', 'H1'])
        self.assertEqual([c['rank'] for c in merged], list(range(1, 21)))
        self.assertEqual([c['id'] for c in merged], list(range(1, 21)))


if __name__ == '__main__':
    unittest.main()
