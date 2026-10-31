"""Aprender com o 👍/👎: a nota dos trechos se aproxima dos cortes aprovados, por canal quando dá."""
from __future__ import annotations

import unittest
from unittest import mock

from app import preference, selection, service, storage
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


def f(gancho, duracao_s, interesse=10.0):
    return {'gancho': gancho, 'duracao_s': duracao_s, 'interesse': interesse}


GOOD = [('bom', f(18, 30)), ('bom', f(20, 28)), ('bom', f(17, 33))]
BAD = [('rejeitado', f(5, 60)), ('rejeitado', f(4, 55)), ('rejeitado', f(6, 62))]


class ModelTests(unittest.TestCase):
    def build(self, projects: dict, channel=''):
        """``projects``: id -> (canal, amostras)."""
        with mock.patch.object(storage, 'list_projects', lambda: [{'id': k} for k in projects]), \
                mock.patch.object(storage, 'read_project',
                                  lambda pid: {'channel': projects[pid][0], 'feedback_log': [{}]}), \
                mock.patch.object(preference, '_samples', lambda pid: projects[pid][1]):
            return preference.build(channel)

    def test_learns_what_separates_approved_from_rejected(self):
        model = self.build({'a': ('Coringa', GOOD + BAD)})
        self.assertEqual((model['good'], model['bad']), (3, 3))
        self.assertIn('gancho', model['weights'])
        self.assertNotIn('interesse', model['weights'])   # igual nos dois lados: não separa nada
        self.assertGreater(preference.adjust(model, {'gancho': 19}, 30), 3)
        self.assertLess(preference.adjust(model, {'gancho': 5}, 60), -3)
        self.assertLessEqual(abs(preference.adjust(model, {'gancho': 100}, 0)), preference.MAX_ADJUST)

    def test_too_few_ratings_learn_nothing(self):
        self.assertIsNone(self.build({'a': ('', GOOD + BAD[:2])}))
        self.assertEqual(preference.adjust(None, {'gancho': 20}, 30), 0.0)

    def test_channel_ratings_win_when_there_are_enough(self):
        flipped = [('bom', s[1]) for s in BAD] + [('rejeitado', s[1]) for s in GOOD]
        projects = {'a': ('Coringa', GOOD + BAD), 'b': ('Outro', flipped * 2)}
        own = self.build(projects, 'coringa')
        self.assertEqual(own['scope'], 'canal')
        self.assertGreater(preference.adjust(own, {'gancho': 19}, 30), 0)
        everyone = self.build(projects, 'canal-novo')
        self.assertEqual((everyone['scope'], everyone['good']), ('todos', 9))

    def test_channel_comes_from_twitch_when_not_typed(self):
        self.assertEqual(preference.channel_of({'community': {'video': {'channel': 'Coringa'}}}), 'Coringa')
        self.assertEqual(preference.channel_of({'channel': 'X', 'community': {'video': {'channel': 'Y'}}}), 'X')
        self.assertEqual(preference.channel_of({}), '')


class SelectionTests(unittest.TestCase):
    SEGMENTS = [{'start': float(t), 'end': float(t) + 4.5,
                 'text': f'Olha só que história incrível número {t} sobre o jogo de ontem.'} for t in range(0, 300, 5)]

    def test_preference_changes_the_score_and_explains_why(self):
        model = {'weights': {'duracao_s': (2.0, 40.0, 5.0)}, 'good': 3, 'bad': 3, 'scope': 'todos', 'channel': ''}
        clips, info = selection.select_clips(self.SEGMENTS, 300, 35, 3, [], preference=model)
        self.assertEqual(info['preference'], {'good': 3, 'bad': 3, 'scope': 'todos', 'channel': ''})
        self.assertTrue(any('preferencia' in (c['score_breakdown'] or {}) for c in clips))
        plain, info = selection.select_clips(self.SEGMENTS, 300, 35, 3, [])
        self.assertNotIn('preference', info)


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class PreferenceAnalysisTests(ServiceBase):
    def test_ratings_are_used_on_reselect(self):
        res = self.create(make_video(self.tmp / 'pref.mp4', size='320x240', duration=40, fps=25))
        pid = res['id']
        with storage.edit_project(pid) as p:
            p['feedback_log'] = [{'verdict': v, 'start': s, 'end': s + 8.0} for v, s in
                                 [('bom', 0.0), ('bom', 9.0), ('bom', 18.0),
                                  ('rejeitado', 4.0), ('rejeitado', 22.0), ('rejeitado', 30.0)]]
        self.assertEqual(service.set_reference(pid, channel=' Coringa ')['channel'], 'Coringa')
        with mock.patch.object(preference, 'build', wraps=preference.build) as spy:
            job = service.reselect(pid)
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        spy.assert_called_once_with('Coringa')


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class WordLinesTests(ServiceBase):
    def test_subtitle_preview_lines_use_video_time(self):
        res = self.create(make_video(self.tmp / 'words.mp4', size='320x240', duration=12, fps=25))
        pid = res['id']
        with storage.edit_project(pid) as p:
            p['transcript'] = [{'start': 4.0, 'end': 6.0, 'text': 'olha isso mano',
                                'words': [{'word': 'olha', 'start': 4.0, 'end': 4.4},
                                          {'word': 'isso', 'start': 4.5, 'end': 5.0},
                                          {'word': 'mano', 'start': 5.1, 'end': 5.8}]}]
        lines = service.word_lines(pid, 3.0, 9.0)['lines']
        self.assertEqual([(w['text'], w['start']) for w in lines[0]], [('olha', 4.0), ('isso', 4.5), ('mano', 5.1)])
        from app.errors import AppError
        with self.assertRaises(AppError):
            service.word_lines(pid, 5.0, 2.0)


if __name__ == '__main__':
    unittest.main()
