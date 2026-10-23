"""Testes de seleção, legendas e direitos (sem FFmpeg)."""
import unittest

from app.engine import build_candidates, rights_review, score_clip, srt_timestamp
from app import selection, subtitles
from tests.helpers import fake_transcript


class SelectionTests(unittest.TestCase):
    def test_no_transcript_generates_valid_temporal_candidates(self):
        cuts = build_candidates([], duration=180, target=30, desired_count=5)
        self.assertTrue(2 <= len(cuts) <= 5)
        self.assertTrue(all(0 <= c['start'] < c['end'] <= 180 for c in cuts))
        self.assertTrue(all(c['selection_mode'] == 'temporal' and c['score'] is None for c in cuts),
                        'cortes temporais não podem receber nota de análise semântica')

    def test_short_file_one_candidate(self):
        cuts = build_candidates([], duration=7.5, target=30, desired_count=5)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['end'], 7.5)

    def test_long_video_fallback_never_returns_whole_video(self):
        # Regressão: antes, um vídeo de 2 h com pouca fala virava um único corte de 7200 s.
        cuts = build_candidates([{'start': 7197, 'end': 7199, 'text': 'tchau'}], 7200, 30, 3)
        self.assertTrue(all(c['end'] - c['start'] <= 30.01 for c in cuts))

    def test_transcript_clips_start_and_end_on_sentences(self):
        transcript = fake_transcript(120)
        clips, info = selection.select_clips(transcript, 120, 20, 4, [])
        self.assertEqual(info['mode'], 'transcricao')
        self.assertTrue(clips)
        starts = {round(s['start'], 2) for s in transcript}
        ends = {round(s['end'], 2) for s in transcript}
        for c in clips:
            self.assertTrue(any(abs(c['start'] - (s - 0.25)) < 0.3 or abs(c['start'] - s) < 0.3 for s in starts))
            self.assertTrue(any(0 <= c['end'] - e <= 0.45 for e in ends))
            self.assertIsNotNone(c['score'])
            self.assertTrue(c['reasons'])

    def test_no_duplicate_or_overlapping_clips(self):
        clips, _ = selection.select_clips(fake_transcript(200), 200, 15, 10, [])
        for i, a in enumerate(clips):
            for b in clips[i + 1:]:
                inter = max(0, min(a['end'], b['end']) - max(a['start'], b['start']))
                self.assertLessEqual(inter / min(a['end'] - a['start'], b['end'] - b['start']), 0.21)

    def test_ranked_best_first(self):
        clips, _ = selection.select_clips(fake_transcript(150), 150, 20, 5, [])
        scores = [c['score'] for c in clips]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([c['id'] for c in clips], list(range(1, len(clips) + 1)))

    def test_hook_terms_match_whole_words(self):
        # Regressão: "mas " casava dentro de "formas ".
        self.assertNotIn('mas', selection.tokens('Existem várias formas de fazer'))

    def test_score_clip_compat(self):
        self.assertIsNone(score_clip([], 0, 10, []))
        self.assertGreater(score_clip(fake_transcript(30), 0, 20, []), 0)

    def test_insufficient_speech_is_honest(self):
        clips, info = selection.select_clips([{'start': 1, 'end': 2, 'text': 'Oi.'}], 100, 30, 3)
        self.assertEqual(info['mode'], 'temporal')
        self.assertIn('insuficiente', info['reason'])

    def test_ollama_disabled_or_offline_never_crashes(self):
        from app import config
        clips, _ = selection.select_clips(fake_transcript(60), 60, 20, 2)
        before = [dict(c) for c in clips]
        self.assertEqual(selection.refine_with_ollama(clips)['state'], 'disabled')
        old = (config.OLLAMA_ENABLED, config.OLLAMA_URL)
        try:
            config.OLLAMA_ENABLED, config.OLLAMA_URL = True, 'http://127.0.0.1:9'  # porta fechada
            status = selection.refine_with_ollama(clips)
        finally:
            config.OLLAMA_ENABLED, config.OLLAMA_URL = old
        self.assertEqual(status['state'], 'unavailable')
        self.assertEqual([c['score'] for c in clips], [c['score'] for c in before])

    def test_ollama_invalid_json_and_ungrounded_title(self):
        from app import config
        unique = [f'Assunto número {n}: a equipe {w} analisou dados novos sobre {w} hoje.'
                  for n, w in enumerate(['clima', 'mercado', 'futebol', 'saúde', 'educação', 'música', 'cinema',
                                         'ciência', 'economia', 'turismo', 'tecnologia', 'culinária'])]
        clips, _ = selection.select_clips(fake_transcript(150, unique), 150, 20, 2)
        self.assertEqual(len(clips), 2)
        responses = iter([{'models': [{'name': 'llama3.2:latest'}]},
                          {'response': 'isto não é json'},
                          {'response': '{"title": "Bitcoin vai a 1 milhão amanhã", "score": 90, '
                                       '"complete_thought": true, "reason": "ok"}'}])
        original, old = selection._ollama, config.OLLAMA_ENABLED
        selection._ollama = lambda *a, **k: next(responses)
        config.OLLAMA_ENABLED = True
        try:
            status = selection.refine_with_ollama(clips)
        finally:
            selection._ollama, config.OLLAMA_ENABLED = original, old
        self.assertEqual(status['state'], 'partial')
        self.assertTrue(all('Bitcoin' not in c['title'] for c in clips), 'título inventado não pode ser aceito')


class SubtitleTests(unittest.TestCase):
    def test_time_format(self):
        self.assertEqual(srt_timestamp(62.35), '00:01:02,350')

    def test_cues_two_lines_max_and_relative(self):
        transcript = [{'start': 10, 'end': 16, 'text': 'Esta é uma frase de exemplo bem comprida que o reconhecedor '
                                                      'costuma gerar em um único segmento de fala contínua.'}]
        style = subtitles.style_for(1080, 1920)
        cues = subtitles.build_cues(transcript, 9, 20, style['max_chars'])
        self.assertGreater(len(cues), 1)
        for c in cues:
            self.assertLessEqual(c['text'].count('\n'), 1)
            self.assertTrue(all(len(line) <= style['max_chars'] for line in c['text'].split('\n')))
            self.assertGreaterEqual(c['start'], 0)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a['end'], b['start'])

    def test_ass_escapes_override_tags_and_uses_output_resolution(self):
        cues = [{'start': 0, 'end': 1, 'text': subtitles.clean_text('Olá {\\b1}mundo\\N <i>x</i> ação')}]
        ass = subtitles.to_ass(cues, 1080, 1920)
        self.assertIn('PlayResY: 1920', ass)
        dialogue = ass.strip().splitlines()[-1]
        self.assertNotIn('{', dialogue)
        self.assertIn('ação', dialogue)


class RightsTests(unittest.TestCase):
    def test_declared_owned_never_claims_clearance(self):
        review = rights_review('owned', 'no', '', True, graphics='none', third_party_footage='no', monetization='no')
        self.assertEqual(review['level'], 'informado_sem_alertas')
        self.assertIn('não garante', review['disclaimer'])
        self.assertIn('declara', review['confidence'])
        self.assertNotIn('livre', ' '.join(i['status'] for i in review['items']))

    def test_third_party_is_high_and_requires_ack(self):
        review = rights_review('third_party', 'yes', '', True)
        self.assertEqual(review['level'], 'alto')
        self.assertTrue(review['requires_acknowledgement'])

    def test_unrecognized_music_is_not_free(self):
        review = rights_review('owned', 'unknown', '', True, 'none', 'no', 'yes',
                               music_review={'status': 'no_match', 'matches': []})
        music = next(i for i in review['items'] if i['key'] == 'musica_audio')
        self.assertEqual(music['status'], 'nao_verificado')
        self.assertIn('NÃO indica', music['reason'])

    def test_music_match_raises_risk(self):
        review = rights_review('owned', 'no', '', True, 'none', 'no', 'yes',
                               music_review={'status': 'matched', 'matches': [
                                   {'title': 'Canção', 'artist': 'Artista', 'uncertain': True}]})
        self.assertEqual(review['level'], 'alto')

    def test_categories_are_separate(self):
        keys = [i['key'] for i in rights_review('unknown', 'unknown', '', True)['items']]
        self.assertEqual(keys, ['video_original', 'musica_audio', 'imagens_graficos', 'audiovisual_terceiros',
                                'conteudo_reutilizado', 'monetizacao'])

    def test_monetization_never_confirmed(self):
        review = rights_review('owned', 'no', 'x', True, 'none', 'no', 'yes')
        mon = next(i for i in review['items'] if i['key'] == 'monetizacao')
        self.assertNotEqual(mon['status'], 'declarado')


class MusicTests(unittest.TestCase):
    def test_music_fails_closed_without_optin(self):
        from pathlib import Path
        from app.music import recognize_music
        out = recognize_music(Path('nao_existe.mp4'), 120, False, True)
        self.assertEqual(out['status'], 'not_requested')
        self.assertEqual(out['matches'], [])

    def test_not_configured_says_not_performed(self):
        import os
        from pathlib import Path
        from app.music import recognize_music
        saved = {k: os.environ.pop(k, None) for k in ('ACRCLOUD_HOST', 'ACRCLOUD_ACCESS_KEY', 'ACRCLOUD_ACCESS_SECRET')}
        try:
            out = recognize_music(Path('x.mp4'), 120, True, True)
        finally:
            os.environ.update({k: v for k, v in saved.items() if v})
        self.assertEqual(out['status'], 'not_configured')
        self.assertIn('NÃO', out['message'])


if __name__ == '__main__':
    unittest.main()
