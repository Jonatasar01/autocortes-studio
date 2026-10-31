"""Clipes do público na Twitch: busca na API, encaixe pelo áudio e prioridade na lista de cortes."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

import numpy as np

from app import community, service, storage
from app.errors import AppError
from tests.helpers import HAS_FFMPEG, make_video
from tests.test_service import ServiceBase

VIDEO = {'id': '2277000111', 'title': 'Live', 'channel': 'Canal', 'duration': 7200.0}


def speech(seconds: float, seed: int = 1, hop: float = 0.2) -> list[float]:
    """Energia parecida com fala: sílabas altas, pausas, frases."""
    rng = np.random.default_rng(seed)
    n = int(seconds / hop)
    base = -30 + 8 * np.sin(np.arange(n) / 7.0) + rng.normal(0, 6, n)
    base[rng.random(n) < 0.15] = -60   # pausas
    return list(np.clip(base, -80, 0))


def clip(cid, views, offset, duration=30.0, title=''):
    return {'id': cid, 'title': title, 'views': views, 'duration': duration, 'offset': offset, 'creator': 'x'}


class ParseTests(unittest.TestCase):
    def test_vod_link(self):
        for value in ('https://www.twitch.tv/videos/2277000111', 'twitch.tv/videos/2277000111?t=1h2m',
                      'https://m.twitch.tv/videos/2277000111/', '2277000111'):
            with self.subTest(value=value):
                self.assertEqual(community.parse_vod(value), '2277000111')
        self.assertIsNone(community.parse_vod('  '))
        for bad in ('https://clips.twitch.tv/AbcDef', 'https://www.twitch.tv/canal', 'https://evil.tv/videos/123456'):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                community.parse_vod(bad)

    def test_duration(self):
        self.assertEqual(community.parse_duration('3h2m1s'), 10921.0)
        self.assertEqual(community.parse_duration('45s'), 45.0)
        self.assertEqual(community.parse_duration('junk'), 0.0)


class LocateTests(unittest.TestCase):
    def setUp(self):
        self.track = speech(3600)

    def piece(self, at: float, seconds: float = 30.0, gain: float = -7.0, noise: float = 1.5):
        k = int(at / 0.2)
        rng = np.random.default_rng(9)
        part = np.asarray(self.track[k:k + int(seconds / 0.2)]) + gain + rng.normal(0, noise, int(seconds / 0.2))
        return list(part)

    def test_finds_the_clip_anywhere_in_the_video(self):
        pos, corr = community.locate(self.piece(1234.4), self.track, 0.2)
        self.assertAlmostEqual(pos, 1234.4, delta=0.21)
        self.assertGreater(corr, 0.9)

    def test_twitch_offset_wrong_by_a_minute(self):
        pos, _ = community.locate(self.piece(2000.0), self.track, 0.2, expected=2060.0)
        self.assertAlmostEqual(pos, 2000.0, delta=0.21)

    def test_unrelated_or_silent_audio_is_not_placed(self):
        self.assertIsNone(community.locate(speech(30, seed=77), self.track, 0.2))
        self.assertIsNone(community.locate([-80.0] * 150, self.track, 0.2))
        self.assertIsNone(community.locate(self.piece(100.0, seconds=3), self.track, 0.2))


class FetchTests(unittest.TestCase):
    def test_only_clips_of_this_live_most_viewed_first(self):
        pages = [
            {'data': [{'id': 'A', 'video_id': VIDEO['id'], 'vod_offset': 100, 'view_count': 5, 'duration': 30,
                       'title': 'eita'},
                      {'id': 'B', 'video_id': '999', 'vod_offset': 50, 'view_count': 900, 'duration': 30}],
             'pagination': {'cursor': 'c1'}},
            {'data': [{'id': 'C', 'video_id': VIDEO['id'], 'vod_offset': None, 'view_count': 70, 'duration': 20.5}],
             'pagination': {}},
        ]
        calls = []

        def helix(path, params):
            calls.append((path, params))
            if path == '/videos':
                return {'data': [{'user_id': '42', 'user_name': 'Canal', 'title': 'Live',
                                  'created_at': '2026-10-01T20:00:00Z', 'duration': '2h0m0s'}]}
            return pages[len([c for c in calls if c[0] == '/clips']) - 1]

        with mock.patch.object(community, '_helix', helix):
            video, clips = community.fetch_clips(VIDEO['id'])
        self.assertEqual(video['duration'], 7200.0)
        self.assertEqual([(c['id'], c['views'], c['offset']) for c in clips], [('C', 70, None), ('A', 5, 100.0)])
        clip_calls = [p for path, p in calls if path == '/clips']
        self.assertEqual((clip_calls[0]['broadcaster_id'], clip_calls[0]['started_at'], clip_calls[1]['after']),
                         ('42', '2026-10-01T20:00:00Z', 'c1'))

    def test_missing_video_and_missing_keys(self):
        with mock.patch.object(community, '_helix', lambda path, params: {'data': []}):
            with self.assertRaises(AppError) as err:
                community.fetch_clips('123456')
        self.assertEqual(err.exception.code, 'twitch_no_video')
        with mock.patch.object(community, 'credentials', lambda: ('', '')), self.assertRaises(AppError) as err:
            community.fetch_clips('123456')
        self.assertEqual(err.exception.code, 'twitch_no_key')

    def test_expired_token_is_renewed_once(self):
        community._token.clear()
        answers = [{'access_token': 'old', 'expires_in': 3600},
                   urllib.error.HTTPError('u', 401, 'x', {}, io.BytesIO()),
                   {'access_token': 'new', 'expires_in': 3600},
                   {'data': ['ok']}]
        seen = []

        def fake_urlopen(req, timeout):
            seen.append(dict(req.header_items()).get('Authorization'))
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return io.BytesIO(json.dumps(answer).encode())

        with mock.patch.object(community, 'credentials', lambda: ('id' * 15, 's' * 30)), \
                mock.patch('urllib.request.urlopen', fake_urlopen):
            self.assertEqual(community._helix('/videos', {'id': '1'}), {'data': ['ok']})
        self.assertEqual(seen, [None, 'Bearer old', None, 'Bearer new'])
        community._token.clear()


class AlignTests(unittest.TestCase):
    def test_without_yt_dlp_only_a_full_live_uses_the_twitch_offset(self):
        clips = [clip('A', 10, 100.0), clip('B', 5, None)]
        with mock.patch.object(community, 'aligner_available', lambda: False):
            full = community.align(clips, VIDEO, 7210.0, None)
            part = community.align(clips, VIDEO, 1800.0, None)
        self.assertEqual([(c['start'], c['end'], c['method']) for c in full],
                         [(100.0, 130.0, 'twitch'), (None, None, None)])
        self.assertTrue(all(c['start'] is None for c in part))

    def test_audio_places_clips_in_a_piece_of_the_live(self):
        track = {'hop': 0.2, 'db': speech(1800)}
        energy = {'A': track['db'][2500:2650], 'B': speech(30, seed=5)}
        with mock.patch.object(community, 'aligner_available', lambda: True), \
                mock.patch.object(community, '_clip_energy', lambda cid, ev: energy[cid]):
            out = community.align([clip('A', 10, 4000.0), clip('B', 3, 50.0)], VIDEO, 1800.0, track)
        self.assertEqual((out[0]['start'], out[0]['end'], out[0]['method']), (500.0, 530.0, 'audio'))
        self.assertIsNone(out[1]['start'])

    def test_previous_matches_are_reused(self):
        prev = {'A': {'start': 10.0, 'end': 40.0, 'method': 'audio', 'match': 0.9}}
        with mock.patch.object(community, 'aligner_available', lambda: True), \
                mock.patch.object(community, '_clip_energy', side_effect=AssertionError('baixou de novo')):
            out = community.align([clip('A', 1, None)], VIDEO, 1800.0, {'hop': 0.2, 'db': [0.0]}, previous=prev)
        self.assertEqual(out[0]['start'], 10.0)

    def test_clips_of_the_same_moment_are_grouped(self):
        aligned = [dict(clip('A', 100, 0), start=60.0, end=90.0, method='twitch'),
                   dict(clip('B', 40, 0), start=65.0, end=95.0, method='audio'),
                   dict(clip('C', 300, 0, title='a melhor'), start=400.0, end=430.0, method='audio'),
                   dict(clip('D', 1, 0), start=None, end=None, method=None)]
        found = community.moments(aligned)
        self.assertEqual([(m['start'], m['end'], m['views'], m['count'], m['method']) for m in found],
                         [(400.0, 430.0, 300, 1, 'audio'), (60.0, 95.0, 140, 2, 'audio')])


class ApplyTests(unittest.TestCase):
    SEGMENTS = [{'start': float(t), 'end': float(t) + 4.5, 'text': f'Frase número {t} aqui.'} for t in range(0, 600, 5)]

    def test_backed_clips_go_first_and_new_moments_become_clips(self):
        clips = [{'id': 1, 'start': 0.0, 'end': 30.0, 'score': 90, 'sources': ['heuristica'], 'reasons': [],
                  'title': 'Primeiro'},
                 {'id': 2, 'start': 200.0, 'end': 240.0, 'score': 80, 'sources': ['ia'], 'reasons': ['bom'],
                  'title': 'Segundo'}]
        found = [{'start': 402.0, 'end': 431.0, 'views': 900, 'count': 3, 'title': 'QUE ISSO', 'method': 'audio',
                  'clips': ['X']},
                 {'start': 205.0, 'end': 230.0, 'views': 50, 'count': 1, 'title': 'ok', 'method': 'twitch',
                  'clips': ['Y']}]
        out = community.apply(clips, found, self.SEGMENTS, 600.0, [], 35)
        self.assertEqual([c['title'] for c in out], ['QUE ISSO', 'Segundo', 'Primeiro'])
        self.assertEqual([c['id'] for c in out], [1, 2, 3])
        new, twin = out[0], out[1]
        self.assertEqual(new['sources'], ['publico'])
        self.assertLessEqual(new['start'], 399.0)
        self.assertGreaterEqual(new['end'], 433.0)
        self.assertGreaterEqual(new['end'] - new['start'], 35)
        self.assertIsNotNone(new['score'])
        self.assertEqual(twin['sources'], ['ia', 'publico'])
        self.assertEqual(twin['community']['views'], 50)
        self.assertTrue(twin['reasons'][0].startswith('1 clipe(s) do público'))
        self.assertIn('ponto informado pela Twitch', twin['evidence'][0]['detalhe'])
        self.assertNotIn('community', out[2])


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class CommunityAnalysisTests(ServiceBase):
    def test_reselect_puts_the_twitch_moment_first(self):
        res = self.create(make_video(self.tmp / 'live.mp4', size='640x360', duration=40, fps=25))
        pid = res['id']
        self.assertEqual(service.set_reference(pid, 'https://www.twitch.tv/videos/2277000111'),
                         {'twitch_vod': '2277000111', 'channel': None})
        video = dict(VIDEO, duration=40.0)
        with mock.patch.object(community, 'fetch_clips', lambda vid: (video, [clip('Z', 77, 12.0, 10.0, 'lance')])), \
                mock.patch.object(community, 'aligner_available', lambda: False):
            job = service.reselect(pid)
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        p = service.get_project(pid)
        self.assertEqual(p['community']['matched'], 1)
        self.assertNotIn('clips', p['community'])
        first = p['clips'][0]
        self.assertIn('publico', first['sources'])
        self.assertLessEqual(first['start'], 12.0)
        self.assertGreaterEqual(first['end'], 22.0)
        # Sem a Twitch (erro de rede), os cortes ficam como estavam e o motivo aparece.
        with mock.patch.object(community, 'fetch_clips',
                               side_effect=AppError('Não foi possível conectar à Twitch.', 502, 'twitch_unreachable')):
            job = service.reselect(pid)
            self.assertEqual(service.manager.wait(job['id'], 180)['status'], 'done')
        p = storage.read_project(pid)
        self.assertEqual(p['community']['state'], 'failed')
        self.assertTrue(p['clips'])
        service.set_reference(pid, '')
        self.assertNotIn('community', storage.read_project(pid))

    def test_imported_twitch_live_keeps_the_link_and_channel(self):
        from tests.test_service import OWNED
        video = make_video(self.tmp / 'importada.mp4', size='320x240', duration=8, fps=25)
        upload = service.Upload('Live.mp4', dict(OWNED), video.stat().st_size, origin='twitch')
        upload.provenance = {'url': 'https://www.twitch.tv/videos/2277000111', 'title': 'Live', 'uploader': 'Coringa',
                             'twitch_vod': '2277000111'}
        upload.write(video.read_bytes())
        res = upload.finish()
        service.manager.wait(res['job']['id'], 180)
        p = storage.read_project(res['id'])
        self.assertEqual((p['twitch_vod'], p['channel'], p['origin']), ('2277000111', 'Coringa', 'twitch'))

    def test_bad_link(self):
        res = self.create(make_video(self.tmp / 'live2.mp4', size='320x240', duration=12, fps=25))
        with self.assertRaises(AppError):
            service.set_reference(res['id'], 'https://clips.twitch.tv/Abc')


if __name__ == '__main__':
    unittest.main()
