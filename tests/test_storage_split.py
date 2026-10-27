"""A transcrição fica em transcript.json: edições não regravam vários MB, e projetos antigos migram sozinhos."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, storage


class StorageSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='split-'))
        patcher = mock.patch.object(config, 'DATA_DIR', self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pid, self.folder = storage.new_project_dir()

    def test_old_projects_migrate_and_edits_skip_the_transcript(self):
        transcript = [{'start': i, 'end': i + 1, 'text': f'frase {i}'} for i in range(500)]
        (self.folder / 'project.json').write_text(json.dumps({'id': self.pid, 'clips': [{'id': 1, 'title': 'a'}],
                                                              'transcript': transcript}), encoding='utf-8')
        p = storage.read_project(self.pid)
        self.assertEqual(p['transcript'], transcript)
        storage.write_project(self.pid, p)                            # migra
        self.assertNotIn('transcript', json.loads((self.folder / 'project.json').read_text(encoding='utf-8')))
        self.assertEqual(json.loads((self.folder / 'transcript.json').read_text(encoding='utf-8')), transcript)
        stamp = (self.folder / 'transcript.json').stat().st_mtime_ns
        with storage.edit_project(self.pid) as p:                     # edição comum: não regrava a transcrição
            p['clips'][0]['title'] = 'b'
        self.assertEqual((self.folder / 'transcript.json').stat().st_mtime_ns, stamp)
        self.assertEqual(storage.read_project(self.pid)['clips'][0]['title'], 'b')
        self.assertEqual(len(storage.read_project(self.pid)['transcript']), 500)

    def test_replacing_and_removing_the_transcript(self):
        storage.write_project(self.pid, {'id': self.pid, 'transcript': [{'start': 0, 'end': 1, 'text': 'um'}]})
        with storage.edit_project(self.pid) as p:
            p['transcript'] = [{'start': 0, 'end': 2, 'text': 'dois'}]
        self.assertEqual(storage.read_project(self.pid)['transcript'][0]['text'], 'dois')
        with storage.edit_project(self.pid) as p:
            p.pop('transcript')
        self.assertNotIn('transcript', storage.read_project(self.pid))
        self.assertFalse((self.folder / 'transcript.json').exists())

    def test_project_json_is_compact(self):
        storage.write_project(self.pid, {'id': self.pid, 'clips': [{'id': 1, 'title': 'x'}]})
        text = (self.folder / 'project.json').read_text(encoding='utf-8')
        self.assertNotIn('\n', text)


if __name__ == '__main__':
    unittest.main()
