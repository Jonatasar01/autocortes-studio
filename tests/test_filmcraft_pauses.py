"""Etapa opcional FilmCraft: transcript.set + transcript.removePauses (sem FFmpeg nem FilmCraft reais)."""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path
from unittest import mock

import pytest

from app import advanced, config, service, storage
from app.errors import AppError
from app.integrations.filmcraft.bridge import (
    PAUSE_DEFAULTS, TICKS, BridgeError, FilmCraftBridge, normalize_pause_options, transcript_payload)

FPS = Fraction(30)
F = TICKS // 30  # ticks por quadro a 30 fps


# ----------------------------------------------------------------------------- opções

def test_pause_options_off_and_defaults():
    assert normalize_pause_options(None) is None
    assert normalize_pause_options(False) is None
    assert normalize_pause_options(True) == PAUSE_DEFAULTS
    assert normalize_pause_options({}) == PAUSE_DEFAULTS
    assert normalize_pause_options({'min_seconds': 1, 'keep_seconds': 0.2}) == {'min_seconds': 1.0, 'keep_seconds': 0.2}


@pytest.mark.parametrize('options', [
    'sim', 3, ['min_seconds'], {'min_seconds': '1'}, {'min_seconds': True}, {'min_seconds': float('nan')},
    {'min_seconds': float('inf')}, {'min_seconds': 0.2}, {'min_seconds': 5.1}, {'keep_seconds': -0.1},
    {'keep_seconds': 1.1}, {'min_seconds': 0.5, 'keep_seconds': 0.25}, {'min_seconds': 0.6, 'keep_seconds': 0.4},
    {'minSeconds': 1}, {'min_seconds': 1, 'extra': 1}])
def test_pause_options_rejected(options):
    with pytest.raises(BridgeError):
        normalize_pause_options(options)


# ----------------------------------------------------------------------------- palavras

def word(text, start, end):
    return {'text': text, 'start_ms': start, 'end_ms': end}


def test_transcript_payload_converts_to_exact_ticks_sorted_and_trimmed():
    clips = [{'words': [word('mundo', 2000, 2500), word(' Olá ', 1001, 1400), word('   ', 3000, 3100)]}]
    payload = transcript_payload(clips, 'pt')
    assert payload['language'] == 'pt' and payload['speakers'] == []
    assert payload['words'] == [{'text': 'Olá', 'start': 1001 * 254_016_000, 'end': 1400 * 254_016_000},
                                {'text': 'mundo', 'start': 2000 * 254_016_000, 'end': 2500 * 254_016_000}]


def test_transcript_payload_deduplicates_overlapping_clips_and_defaults_language():
    clips = [{'words': [word('a', 0, 500), word('b', 500, 900)]}, {'words': [word('b', 500, 900), word('c', 900, 1200)]}]
    payload = transcript_payload(clips, None)
    assert [w['text'] for w in payload['words']] == ['a', 'b', 'c']
    assert payload['language'] == 'und'


@pytest.mark.parametrize('clips', [
    [], [{}], [{'words': []}], [{'words': ['x']}], [{'words': [word('a', 5, 5)]}], [{'words': [word('a', 6, 5)]}],
    [{'words': [word('a', -1, 5)]}], [{'words': [word('a', 1.5, 5)]}], [{'words': [word('a', True, 5)]}],
    [{'words': [word('   ', 0, 5)]}]])
def test_transcript_payload_rejects_unusable_words(clips):
    with pytest.raises(BridgeError):
        transcript_payload(clips, 'pt')


# ----------------------------------------------------------------------------- manifesto

@pytest.fixture
def project(tmp_path, monkeypatch):
    folder = tmp_path / 'proj'
    folder.mkdir()
    (folder / 'source.mp4').write_bytes(b'fake-media')
    data = {'status': 'ready', 'filename': 'source.mp4', 'rights': None, 'transcription': {'language': 'pt'},
            'clips': [{'id': 1, 'start': 1.0, 'end': 5.0, 'title': 'A', 'score': 80, 'revision': 1}],
            'transcript': [{'start': 0.5, 'end': 5.5, 'text': 'Antes Olá mundo cortada', 'words': [
                {'start': 0.5, 'end': 0.9, 'word': 'Antes'}, {'start': 1.0, 'end': 1.4, 'word': ' Olá '},
                {'start': 2.0, 'end': 2.5, 'word': 'mundo'}, {'start': 3.0, 'end': 3.0, 'word': 'zero'},
                {'start': 3.1, 'end': 3.4, 'word': '  '}, {'start': 4.8, 'end': 5.2, 'word': 'cortada'}]}]}
    monkeypatch.setattr(storage, 'read_project', lambda pid: data)
    monkeypatch.setattr(storage, 'project_dir', lambda pid: folder)
    probe = json.dumps({'streams': [{'codec_type': 'video', 'avg_frame_rate': '30000/1001', 'width': 640, 'height': 360}]})
    monkeypatch.setattr(FilmCraftBridge, '_run', lambda self, args, program=None: probe)
    return data


def test_manifest_without_option_is_unchanged(project):
    manifest = FilmCraftBridge().decision_from_project('p' * 32)
    assert 'pause_removal' not in manifest and 'transcript_language' not in manifest
    assert 'words' not in manifest['clips'][0]


def test_manifest_with_option_carries_only_words_inside_the_clip(project):
    manifest = FilmCraftBridge().decision_from_project('p' * 32, pause_removal=True)
    assert manifest['pause_removal'] == PAUSE_DEFAULTS
    assert manifest['transcript_language'] == 'pt'
    assert manifest['clips'][0]['words'] == [word('Olá', 1000, 1400), word('mundo', 2000, 2500)]


def test_manifest_rejects_bad_pause_options(project):
    with pytest.raises(BridgeError):
        FilmCraftBridge().decision_from_project('p' * 32, pause_removal={'min_seconds': 0.1})


def test_manifest_unknown_language_becomes_none(project):
    project['transcription'] = {'language': '?'}
    assert FilmCraftBridge().decision_from_project('p' * 32, pause_removal=True)['transcript_language'] is None


# ----------------------------------------------------------------------------- validação da linha do tempo

def clip(start, end, source_in, source_out):
    return {'start': start * F, 'end': end * F, 'sourceIn': source_in * F, 'sourceOut': source_out * F}


def sequence(video, audio='same'):
    return {'video': [{'items': video}], 'audio': [{'items': video if audio == 'same' else audio}]}


EXPECTED = [clip(0, 100, 200, 300)]
BEFORE = sequence(EXPECTED)
GOOD_AFTER = [clip(0, 40, 200, 240), clip(40, 70, 270, 300)]
GOOD_RESULT = {'removed': 1, 'seconds': 1.0, 'ticks': 30 * F}


def validate(after, result=GOOD_RESULT, expected=EXPECTED, audio=True, before=BEFORE):
    return FilmCraftBridge._validate_pause_removal(before, after, expected, result, FPS, audio)


def test_validation_accepts_a_consistent_cut():
    report = validate(sequence(GOOD_AFTER))
    assert report['removed'] == 1 and report['duration_after_ticks'] == 70 * F
    assert report['duration_before'] == round(100 * F / TICKS, 3) and report['duration_after'] == round(70 * F / TICKS, 3)


def test_validation_accepts_nothing_removed():
    assert validate(sequence(EXPECTED), {'removed': 0, 'ticks': 0})['duration_after_ticks'] == 100 * F


def test_validation_accepts_cut_across_two_original_clips():
    expected = [clip(0, 50, 200, 250), clip(50, 100, 400, 450)]
    after = sequence([clip(0, 30, 200, 230), clip(30, 40, 240, 250), clip(40, 70, 420, 450)])
    assert validate(after, {'removed': 2, 'ticks': 30 * F}, expected, before=sequence(expected))['duration_after_ticks'] == 70 * F


@pytest.mark.parametrize('after, message', [
    (sequence([clip(0, 40, 200, 240), clip(41, 71, 270, 300)]), 'lacuna'),
    (sequence([clip(0, 40, 200, 240), clip(39, 69, 270, 300)]), 'lacuna'),
    (sequence([clip(0, 40, 200, 240), clip(40, 70, 270, 301)]), 'original'),
    (sequence([clip(0, 40, 199, 239), clip(40, 70, 270, 300)]), 'original'),
    (sequence([clip(0, 30, 270, 300), clip(30, 70, 200, 240)]), 'original'),
    (sequence([]), 'esvaziou'),
    (sequence(GOOD_AFTER, [clip(0, 40, 200, 240)]), 'divergem'),
    (sequence(GOOD_AFTER, [clip(0, 40, 200, 240), clip(40, 70, 271, 301)]), 'divergem'),
])
def test_validation_rejects_inconsistent_timelines(after, message):
    with pytest.raises(BridgeError, match=message):
        validate(after)


@pytest.mark.parametrize('result', [
    None, {}, {'ticks': True}, {'ticks': -1}, {'ticks': 'x'}, {'removed': 1, 'ticks': 10 * F}, {'removed': 1, 'ticks': 50 * F}])
def test_validation_rejects_wrong_removed_duration(result):
    with pytest.raises(BridgeError):
        validate(sequence(GOOD_AFTER), result)


def test_validation_rejects_longer_timeline_than_before():
    with pytest.raises(BridgeError, match='diverge'):
        validate(sequence([clip(0, 120, 200, 300)]), {'removed': 0, 'ticks': 0})


def test_validation_without_audio_ignores_audio_tracks():
    after = sequence(GOOD_AFTER, [])
    assert validate(after, audio=False)['removed'] == 1


def test_caption_validation():
    ok = {'tracks': [{'captions': [{'start': 0, 'end': 10 * F}, {'start': 10 * F, 'end': 70 * F}]}]}
    FilmCraftBridge._validate_pause_captions(ok, 70 * F, FPS)
    for bad in ({'tracks': []}, {'tracks': [{'captions': []}]},
                {'tracks': [{'captions': [{'start': 5, 'end': 5}]}]},
                {'tracks': [{'captions': [{'start': -1, 'end': 5}]}]},
                {'tracks': [{'captions': [{'start': 0, 'end': 72 * F}]}]}):
        with pytest.raises(BridgeError):
            FilmCraftBridge._validate_pause_captions(bad, 70 * F, FPS)


# ----------------------------------------------------------------------------- fase 2 com FilmCraft simulado

class FakeFilmCraft(FilmCraftBridge):
    """Substitui só o processo externo: responde como o CLI e registra o que recebeu."""

    def __init__(self, after, removal=GOOD_RESULT, *, accepted=None, write_srt=True, captions=None):
        super().__init__()
        self.sequences = [BEFORE, after]
        self.removal, self.accepted, self.write_srt = removal, accepted, write_srt
        self.captions = captions if captions is not None else {
            'tracks': [{'captions': [{'start': 0, 'end': 40 * F}, {'start': 40 * F, 'end': 70 * F}]}]}
        self.steps = []

    def _run(self, args, *, program=None):
        if 'inspect' in args:
            return json.dumps(self.sequences.pop(0))
        if 'exec' in args:
            return json.dumps(self.captions)
        assert 'run' in args and '--save' in args
        self.steps = [json.loads(line) for line in Path(args[-1]).read_text(encoding='utf-8').splitlines()]
        out = []
        for number, step in enumerate(self.steps, 1):
            if step['id'] == 'transcript.set':
                result = {'item': step['params']['item'], 'words': self.accepted or len(step['params']['transcript']['words'])}
            elif step['id'] == 'transcript.removePauses':
                result = self.removal
            elif step['id'] == 'captions.export':
                if self.write_srt:
                    Path(step['params']['path']).write_text('1\n00:00:00,000 --> 00:00:01,000\nolá\n', encoding='utf-8')
                result = {}
            else:
                result = {'track': 1}
            out.append(json.dumps({'id': step['id'], 'line': number, 'ok': True, 'result': result}))
        return '\n'.join(out)


WORDS = transcript_payload([{'words': [word('Olá', 1000, 1400), word('mundo', 4000, 4500)]}], 'pt')
OPTIONS = {'min_seconds': 0.7, 'keep_seconds': 0.25}


def run_phase(fake, tmp_path, project='proj.fcproj'):
    return fake.remove_pauses(tmp_path / project, 8, WORDS, OPTIONS, EXPECTED, FPS, True, tmp_path)


def test_phase_sends_words_then_removes_pauses_then_rebuilds_captions(tmp_path):
    fake = FakeFilmCraft(sequence(GOOD_AFTER))
    report = run_phase(fake, tmp_path)
    assert [s['id'] for s in fake.steps] == ['transcript.set', 'transcript.removePauses',
                                             'transcript.createCaptions', 'captions.export']
    assert fake.steps[0]['params'] == {'item': 8, 'transcript': WORDS}
    assert fake.steps[1]['params'] == {'minSeconds': 0.7, 'keepSeconds': 0.25}
    assert fake.steps[3]['params'] == {'path': str(tmp_path / 'captions.srt'), 'format': 'srt'}
    assert report['removed'] == 1 and report['words'] == 2 and report['duration_after_ticks'] == 70 * F
    assert (tmp_path / 'captions.srt').is_file()
    saved = json.loads((tmp_path / 'pause-removal.json').read_text(encoding='utf-8'))
    assert saved['options'] == OPTIONS and saved['duration_after'] == round(70 * F / TICKS, 3)


@pytest.mark.parametrize('fake_kwargs, message', [
    ({'accepted': 1}, 'palavras'),
    ({'write_srt': False}, 'SRT'),
    ({'captions': {'tracks': []}}, 'legenda'),
    ({'removal': {'removed': 1, 'ticks': 5 * F}}, 'diverge'),
])
def test_phase_fails_loudly(tmp_path, fake_kwargs, message):
    with pytest.raises(BridgeError, match=message):
        run_phase(FakeFilmCraft(sequence(GOOD_AFTER), **fake_kwargs), tmp_path)


def test_phase_fails_when_the_cli_does_not_run_every_command(tmp_path):
    class Truncated(FakeFilmCraft):
        def _run(self, args, *, program=None):
            text = super()._run(args, program=program)
            return text.splitlines()[0] if 'run' in args else text
    with pytest.raises(BridgeError, match='todos os comandos'):
        run_phase(Truncated(sequence(GOOD_AFTER)), tmp_path)


# ----------------------------------------------------------------------------- build() falha antes de criar saída

def manifest_for(media: Path, **extra):
    manifest = {'schema_version': 'autocortes-edit-v1', 'origin_project_id': 'x',
                'source': {'path': str(media), 'sha256': hashlib.sha256(media.read_bytes()).hexdigest(),
                           'fps': {'num': 30, 'den': 1}},
                'clips': [{'id': 'clip-001', 'source_start_ms': 1000, 'source_end_ms': 5000, 'title': 'A', 'framing': 'fit'}],
                'requested_output': {'width': 640, 'height': 360, 'fps': {'num': 30, 'den': 1}}}
    manifest.update(extra)
    return manifest


@pytest.mark.parametrize('extra, message', [
    ({'pause_removal': {'min_seconds': 0.1}}, 'pausa mínima'),
    ({'pause_removal': True}, 'palavra'),
])
def test_build_rejects_pause_step_before_touching_disk(tmp_path, extra, message):
    media = tmp_path / 'media.mp4'
    media.write_bytes(b'fake-media')
    output = tmp_path / 'out'
    with pytest.raises(BridgeError, match=message):
        FilmCraftBridge().build(manifest_for(media, **extra), output)
    assert not output.exists()


# ----------------------------------------------------------------------------- API e fila

H = {'X-AutoCortes': '1'}
fastapi_testclient = pytest.importorskip('fastapi.testclient')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'data')
    cli = tmp_path / 'filmcraft-cli.exe'
    cli.write_bytes(b'not a real binary')
    monkeypatch.setenv('FILMCRAFT_CLI', str(cli))
    probe = json.dumps({'streams': [{'codec_type': 'video', 'avg_frame_rate': '30/1', 'width': 640, 'height': 360}]})
    monkeypatch.setattr(FilmCraftBridge, '_run', lambda self, args, program=None: probe)
    from app.main import app
    with mock.patch.object(advanced, 'run', lambda ctx: {}):  # a fila não executa o FilmCraft
        with fastapi_testclient.TestClient(app) as test_client:
            yield test_client


def make_project(with_words: bool) -> str:
    project_id, folder = storage.new_project_dir()
    (folder / 'source.mp4').write_bytes(b'fake-media')
    words = [{'start': 1.0, 'end': 1.4, 'word': 'Olá'}, {'start': 3.0, 'end': 3.5, 'word': 'mundo'}] if with_words else []
    storage.write_project(project_id, {
        'id': project_id, 'name': 'p', 'status': 'ready', 'filename': 'source.mp4', 'media': {'duration': 10},
        'transcription': {'language': 'pt'}, 'rights': {},
        'clips': [{'id': 1, 'start': 0.5, 'end': 8.0, 'title': 'A', 'score': 70}],
        'transcript': [{'start': 0.5, 'end': 4.0, 'text': 'Olá mundo', 'words': words}]})
    return project_id


def queued_manifest(response) -> dict:
    return service._jobs().get(response.json()['id'])['params']['manifest']


def test_api_without_option_keeps_previous_behaviour(client):
    response = client.post(f'/api/projects/{make_project(True)}/advanced', json={}, headers=H)
    assert response.status_code == 200
    manifest = queued_manifest(response)
    assert 'pause_removal' not in manifest and 'words' not in manifest['clips'][0]


def test_api_option_queues_manifest_with_words_and_limits(client):
    body = {'remove_pauses': True, 'pause_min_seconds': 1.2, 'pause_keep_seconds': 0.3}
    response = client.post(f'/api/projects/{make_project(True)}/advanced', json=body, headers=H)
    assert response.status_code == 200
    manifest = queued_manifest(response)
    assert manifest['pause_removal'] == {'min_seconds': 1.2, 'keep_seconds': 0.3}
    assert manifest['clips'][0]['words'] == [word('Olá', 1000, 1400), word('mundo', 3000, 3500)]


def test_api_option_without_word_timestamps_is_refused_before_queueing(client):
    project_id = make_project(False)
    response = client.post(f'/api/projects/{project_id}/advanced', json={'remove_pauses': True}, headers=H)
    assert response.status_code == 409 and response.json()['code'] == 'no_words'
    assert service._jobs().for_project(project_id) == []


@pytest.mark.parametrize('body', [
    {'remove_pauses': True, 'pause_min_seconds': 0.1}, {'remove_pauses': True, 'pause_min_seconds': 9},
    {'remove_pauses': True, 'pause_keep_seconds': -1}, {'remove_pauses': True, 'pause_keep_seconds': 2},
    {'remove_pauses': 'talvez'}, {'pausas': True}])
def test_api_rejects_invalid_pause_fields(client, body):
    assert client.post(f'/api/projects/{make_project(True)}/advanced', json=body, headers=H).status_code == 422


def test_api_rejects_keep_that_cancels_the_minimum(client):
    body = {'remove_pauses': True, 'pause_min_seconds': 0.5, 'pause_keep_seconds': 0.25}
    response = client.post(f'/api/projects/{make_project(True)}/advanced', json=body, headers=H)
    assert response.status_code == 400 and response.json()['code'] == 'bad_pause_options'


def test_job_result_exposes_the_pause_summary(tmp_path, monkeypatch):
    report = {'project': 'x', 'render': 'y', 'valid': True,
              'pause_removal': {'removed': 3, 'seconds': 4.5, 'duration_before': 30.0, 'duration_after': 25.5}}
    monkeypatch.setattr(FilmCraftBridge, 'build', lambda self, manifest, target: report)
    monkeypatch.setattr(storage, 'project_dir', lambda pid: tmp_path)
    ctx = mock.Mock(project_id='p' * 32, id='j' * 32, params={'manifest': {}}, cancel_event=mock.Mock(is_set=lambda: False))
    assert advanced.run(ctx)['pause_removal'] == report['pause_removal']
    monkeypatch.setattr(FilmCraftBridge, 'build', lambda self, manifest, target: {'valid': True})
    assert 'pause_removal' not in advanced.run(ctx)


def test_static_ui_sends_the_option():
    static = Path(config.STATIC_DIR)
    assert 'id="advPauses"' in (static / 'index.html').read_text(encoding='utf-8')
    assert 'remove_pauses' in (static / 'app.js').read_text(encoding='utf-8')
