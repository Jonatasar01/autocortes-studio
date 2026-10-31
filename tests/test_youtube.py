"""Publicar no YouTube: login OAuth com PKCE, envio retomável e registro no corte. API do Google simulada."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from app import config, secrets_store, youtube
from app.errors import AppError


class Reply(io.BytesIO):
    def __init__(self, body=b'', status=200, headers=None):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.status, self.headers = status, headers or {}


class YoutubeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.old = config.DATA_DIR
        config.DATA_DIR = self.tmp
        secrets_store.put('youtube_id', 'cliente-123.apps.googleusercontent.com')
        secrets_store.put('youtube_secret', 'GOCSPX-segredo-de-teste-123')
        youtube._pending.clear()
        youtube._access.clear()

    def tearDown(self):
        config.DATA_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_login_uses_pkce_and_keeps_only_the_refresh_token(self):
        url = youtube.auth_url('127.0.0.1:8000')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual((q['redirect_uri'], q['scope'], q['code_challenge_method']),
                         ('http://127.0.0.1:8000/api/youtube/callback', youtube.SCOPE, 'S256'))
        sent = {}

        def fake(req, timeout):
            sent.update(urllib.parse.parse_qsl(req.data.decode()))
            return Reply({'access_token': 'acesso', 'expires_in': 3600, 'refresh_token': 'refresh-token-de-teste-1234'})

        with mock.patch('urllib.request.urlopen', fake):
            youtube.finish(q['state'], 'codigo')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(sent['code_verifier'].encode()).digest()).rstrip(b'=')
        self.assertEqual(challenge.decode(), q['code_challenge'])
        self.assertEqual(secrets_store.get('youtube_refresh'), 'refresh-token-de-teste-1234')
        self.assertTrue(youtube.status()['connected'])
        with self.assertRaises(AppError):   # o mesmo state não vale duas vezes
            youtube.finish(q['state'], 'codigo')

    def test_login_only_from_this_computer(self):
        for host in ('192.168.0.130:8000', 'exemplo.com'):
            with self.subTest(host=host), self.assertRaises(AppError) as err:
                youtube.auth_url(host)
            self.assertEqual(err.exception.code, 'youtube_not_local')
        self.assertTrue(youtube.auth_url('localhost:8000').startswith(youtube.AUTH_URL))

    def test_resumable_upload_continues_after_308(self):
        secrets_store.put('youtube_refresh', 'refresh-token-de-teste-1234')
        youtube._access.update(value='acesso', expires=10 ** 12)
        video = self.tmp / 'clip.mp4'
        video.write_bytes(b'x' * 30)
        calls = []

        def fake(req, timeout):
            calls.append((req.get_method(), dict(req.header_items())))
            if req.get_method() == 'POST':
                meta = json.loads(req.data)
                self.assertEqual((meta['status']['privacyStatus'], meta['snippet']['title']), ('unlisted', 'Título'))
                return Reply(b'', 200, {'Location': 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=1'})
            if len(calls) == 2:   # o primeiro pedaço chegou só até o byte 9
                raise urllib.error.HTTPError(req.full_url, 308, 'Resume Incomplete', {'Range': 'bytes=0-9'}, io.BytesIO())
            return Reply({'id': 'abc123'}, 201)

        progress = []
        with mock.patch('urllib.request.urlopen', fake), mock.patch.object(youtube, 'CHUNK', 16):
            vid = youtube.upload(video, 'Título', 'desc', ['tag'], 'unlisted', on_progress=progress.append)
        self.assertEqual(vid, 'abc123')
        ranges = [h.get('Content-range') for m, h in calls if m == 'PUT']
        self.assertEqual(ranges, ['bytes 0-15/30', 'bytes 10-25/30'])
        self.assertEqual(progress[-1], 1.0)

    def test_expired_connection_has_a_clear_message(self):
        secrets_store.put('youtube_refresh', 'refresh-token-de-teste-1234')

        def fake(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 400, 'x', {}, io.BytesIO(b'{"error": "invalid_grant"}'))

        with mock.patch('urllib.request.urlopen', fake), self.assertRaises(AppError) as err:
            youtube.access_token()
        self.assertIn('conecte a conta de novo', err.exception.message)

    def test_disconnect_forgets_even_offline(self):
        secrets_store.put('youtube_refresh', 'refresh-token-de-teste-1234')
        with mock.patch('urllib.request.urlopen', side_effect=OSError('offline')):
            youtube.disconnect()
        self.assertFalse(youtube.status()['connected'])


if __name__ == '__main__':
    unittest.main()
