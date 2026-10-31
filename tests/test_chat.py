"""Picos no chat da live importado pelo usuário (JSON ou texto do TwitchDownloader)."""
from __future__ import annotations

import json
import random
import unittest
from unittest import mock

from app import chat, community, service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase


def live_chat(seconds: int, bursts: dict[int, int], seed: int = 3) -> list[tuple[float, str]]:
    """Uma mensagem a cada ~2 s, mais rajadas de risada (``segundo: mensagens``)."""
    rng = random.Random(seed)
    out = [(t + rng.random(), 'oi chat') for t in range(0, seconds, 2)]
    for at, n in bursts.items():
        out += [(at + rng.random() * 4, 'KEKW kkkkkk') for _ in range(n)]
    return sorted(out)


def as_json(messages) -> bytes:
    return json.dumps({'video': {}, 'comments': [
        {'content_offset_seconds': t, 'commenter': {'display_name': 'x'}, 'message': {'body': body}}
        for t, body in messages]}).encode()


class ParseTests(unittest.TestCase):
    def test_twitchdownloader_json_and_text(self):
        from_json = chat.parse(as_json([(12.5, 'KEKW'), (3.0, 'boa noite')]))
        self.assertEqual(from_json, [(3.0, False), (12.5, True)])
        text = '[0:00:03] fulano: boa noite\n[1:02:03] ciclano: kkkkkkk\nlinha sem tempo\n'.encode()
        self.assertEqual(chat.parse(text), [(3.0, False), (3723.0, True)])

    def test_bad_files(self):
        for raw in (b'{"comments": [', b'nada aqui', b'{"comments": []}'):
            with self.subTest(raw=raw), self.assertRaises(AppError):
                chat.parse(raw)

    def test_only_times_are_kept(self):
        stored = chat.compact([(1.25, False), (7.0, True)])
        self.assertNotIn('KEKW', json.dumps(stored))
        self.assertEqual(chat.expand(stored), [(1.2, False), (7.0, True)])


class PeakTests(unittest.TestCase):
    def test_bursts_become_moments_that_start_before_the_reaction(self):
        messages = chat.parse(as_json(live_chat(3600, {900: 40, 2400: 25, 2460: 30})))
        found = chat.peaks(messages)
        self.assertEqual(len(found), 2)   # 2400 e 2460 são o mesmo lance (menos de 2 min)
        strongest = found[0]
        self.assertEqual(strongest['start'], 900 - chat.REACTION_DELAY)
        self.assertGreaterEqual(strongest['messages'], 40)
        self.assertGreater(strongest['ratio'], chat.PEAK_RATIO)
        self.assertTrue(2380 <= found[1]['start'] <= 2445)

    def test_a_steady_chat_has_no_peaks(self):
        self.assertEqual(chat.peaks(chat.parse(as_json(live_chat(3600, {})))), [])


class ApplyTests(unittest.TestCase):
    def test_chat_peak_marks_or_creates_a_clip(self):
        clips = [{'id': 1, 'start': 100.0, 'end': 140.0, 'score': 80, 'sources': ['heuristica'], 'reasons': [],
                  'title': 'A'}]
        found = [{'kind': 'chat', 'start': 105.0, 'end': 130.0, 'messages': 50, 'ratio': 4.2},
                 {'kind': 'chat', 'start': 300.0, 'end': 320.0, 'messages': 30, 'ratio': 3.0}]
        out = community.apply(clips, found, [], 600.0, [], 35)
        self.assertEqual([c['sources'] for c in out], [['chat', 'heuristica'], ['chat']])
        self.assertEqual(out[0]['chat_peak'], {'messages': 50, 'ratio': 4.2})
        self.assertIn('4,2× o normal', out[0]['reasons'][0])
        self.assertEqual(out[1]['title'], 'Pico no chat')


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class ChatAnalysisTests(ServiceBase):
    def test_imported_chat_puts_the_peak_first(self):
        res = self.create(make_video(self.tmp / 'chat.mp4', size='320x240', duration=40, fps=25))
        pid = res['id']
        raw = as_json([(t, 'oi') for t in range(0, 40)] + [(30 + k * 0.1, 'KEKW') for k in range(30)])
        out = service.set_chat(pid, raw, 'chat.json')
        self.assertEqual(out['chat']['messages'], 70)
        with mock.patch.object(chat, 'MIN_MESSAGES', 5):
            job = service.reselect(pid)
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        p = storage.read_project(pid)
        self.assertEqual(p['chat']['peaks'], 1)
        self.assertIn('chat', p['clips'][0]['sources'])
        self.assertLessEqual(p['clips'][0]['start'], 30.0)
        service.set_chat(pid, b'')
        self.assertNotIn('chat', storage.read_project(pid))
        self.assertFalse((storage.project_dir(pid) / chat.FILE).exists())

    def test_chat_of_a_longer_live_needs_the_twitch_link(self):
        res = self.create(make_video(self.tmp / 'chat2.mp4', size='320x240', duration=12, fps=25))
        pid = res['id']
        service.set_chat(pid, as_json([(5000.0, 'oi'), (10.0, 'oi')]))
        job = service.reselect(pid)
        self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        self.assertIn('live mais longa', storage.read_project(pid)['chat']['message'])


if __name__ == '__main__':
    unittest.main()
