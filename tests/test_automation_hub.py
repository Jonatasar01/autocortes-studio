import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import automation, config, downloads, settings
from app.errors import AppError
from app.instance import InstanceLock

class AutomationHubTests(unittest.TestCase):
    def test_youtube_urls(self):
        for url in ['https://youtu.be/rYRAvZKm9Fk?feature=share', 'https://www.youtube.com/shorts/rYRAvZKm9Fk', 'https://www.youtube.com/watch?v=rYRAvZKm9Fk&list=abc']:
            self.assertEqual(downloads.canonical_url(url), 'https://www.youtube.com/watch?v=rYRAvZKm9Fk')
        for url in ['http://youtube.com/watch?v=rYRAvZKm9Fk', 'https://127.0.0.1/', 'https://youtube.com.evil.example/shorts/rYRAvZKm9Fk', 'https://user@youtube.com/shorts/rYRAvZKm9Fk', 'https://youtube.com:8000/shorts/rYRAvZKm9Fk', 'https://youtube.com/playlist?list=abc', 'file:///video.mp4']:
            with self.subTest(url=url), self.assertRaises(AppError):
                downloads.canonical_url(url)

    def test_twitch_vod_urls(self):
        for url in ['https://www.twitch.tv/videos/2277000111', 'https://m.twitch.tv/videos/2277000111/?t=1h',
                    'https://twitch.tv/videos/2277000111']:
            self.assertEqual(downloads.canonical_url(url), 'https://www.twitch.tv/videos/2277000111')
        for url in ['https://www.twitch.tv/coringa', 'https://clips.twitch.tv/AbcDef', 'http://www.twitch.tv/videos/2277000111',
                    'https://www.twitch.tv/videos/abc', 'https://twitch.tv.evil.example/videos/2277000111']:
            with self.subTest(url=url), self.assertRaises(AppError):
                downloads.canonical_url(url)

    def test_preset_preserves_consent_and_rights(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, 'DATA_DIR', Path(tmp)):
            original = settings.load()
            with self.assertRaises(AppError):
                automation.apply_preset('narrado')   # perfil de narração removido
            automation.apply_preset('shorts')
            result = settings.load()
            self.assertTrue(result['autopilot']['enabled'])
            self.assertEqual(result['curation'], original['curation'])
            self.assertEqual(result['upload_defaults']['source_rights'], 'unknown')
            with self.assertRaises(AppError):
                automation.apply_preset('missing')

    def test_instance_lock_blocks_other_process_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = 'from pathlib import Path; from app.instance import InstanceLock; import sys; lock=InstanceLock(Path(sys.argv[1])); lock.close()'
            lock = InstanceLock(Path(tmp))
            try:
                blocked = subprocess.run([sys.executable, '-c', code, tmp], capture_output=True, timeout=20)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(b'AppError', blocked.stderr)
            finally:
                lock.close()
            released = subprocess.run([sys.executable, '-c', code, tmp], capture_output=True, timeout=20)
            self.assertEqual(released.returncode, 0, released.stderr)
