"""Regressões da continuação v2.1: segurança, consentimento e concorrência."""
import json
import os
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest
from app import config, media, curation, selection, service, storage, subtitles
from app.errors import AppError, sanitize
from app.jobs import Handler, JobManager
from tests.helpers import HAS_FFMPEG, make_video
from tests import test_service


def test_malformed_origins_and_negative_length():
    mw = test_service.SecurityMiddlewareTests()
    for origin in ('http://[', 'file://localhost', 'http://localhost:bad', 'http://localhost/path'):
        assert mw.call('POST', '/', {'x-autocortes': '1', 'origin': origin})[0] == 403
    assert mw.call('POST', '/', {'x-autocortes': '1', 'content-length': '-1'})[0] == 400


def test_secrets_and_foreign_paths_redacted():
    with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'a-unique-secret'}):
        message = sanitize('a-unique-secret sk-other-secret C:\\Private Folder\\movie.mp4')
    assert 'secret' not in message and 'Private' not in message
    assert '/home/someone/private.mp4' not in sanitize('Failed /home/someone/private.mp4')






def test_subtitle_never_extends_past_clip():
    cues = subtitles.build_cues([{'start': 0.8, 'end': 1, 'text': 'fim'}], 0, 1, 30)
    assert cues and cues[-1]['end'] <= 1


def test_ollama_rejects_string_boolean_and_score():
    clip = {'selection_mode': 'transcricao', 'description': 'Uma frase clara.', 'score': 60}
    for score, complete in [('90', True), (90, 'false'), (True, True)]:
        answers = [{'models': [{'name': 'qwen3:4b'}]}, {'response': json.dumps({
            'title': 'Uma frase', 'score': score, 'complete_thought': complete, 'reason': 'x'})}]
        with mock.patch.object(config, 'OLLAMA_ENABLED', True), mock.patch.object(selection, '_ollama', side_effect=answers):
            assert selection.refine_with_ollama([dict(clip)])['state'] == 'failed'




@pytest.mark.skipif(not HAS_FFMPEG, reason='FFmpeg ausente')
def test_two_concurrent_exports_have_distinct_validated_files(tmp_path):
    src = make_video(tmp_path/'ação com espaço.mp4', duration=2, size='160x90')
    info = media.probe_media(src)
    results, errors = [], []
    def export():
        try:
            results.append(media.export_clip(src, info, tmp_path/'out', 'clip_01', 0, 1.5, fmt='original'))
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=export) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(90)
    assert not errors and len(results) == 2
    assert len({r['file'] for r in results}) == 2
    assert all(r['validation']['valid'] for r in results)


@pytest.mark.skipif(os.name != 'nt' or not HAS_FFMPEG, reason='Windows/FFmpeg necessário')
def test_reexport_with_windows_file_locked(tmp_path):
    import ctypes
    from ctypes import wintypes
    src = make_video(tmp_path/'src.mp4', duration=2, size='160x90')
    info = media.probe_media(src)
    out = tmp_path/'out'
    first = media.export_clip(src, info, out, 'clip_01', 0, 1.5, fmt='original')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(out/first['file']), 0x80000000, 1, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        with pytest.raises(OSError):
            (out/first['file']).unlink()
        second = media.export_clip(src, info, out, 'clip_01', 0, 1.5, fmt='original')
        assert first['file'] != second['file'] and second['validation']['valid']
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != 'nt', reason='Windows necessário')
def test_windows_low_priority_flag():
    import subprocess
    with mock.patch.object(config, 'LOW_PRIORITY', True):
        assert media._popen_kwargs()['creationflags'] & subprocess.BELOW_NORMAL_PRIORITY_CLASS

def test_real_whisper_audio_decoder(tmp_path):
    import wave
    audio = pytest.importorskip('faster_whisper.audio')
    wav = tmp_path/'fala.wav'
    with wave.open(str(wav), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b'\0\0' * 16000)
    assert len(audio.decode_audio(str(wav), sampling_rate=16000)) == 16000

@pytest.mark.parametrize('response', [[], None, {'candidates':[None]}, {'candidates':[{'content':[]} ]}, {'candidates':[{'content':{'parts':[{'text':42}]}}]}])
def test_malformed_gemini_response_is_domain_error(response):
    with mock.patch.dict(os.environ, {'GEMINI_API_KEY':'test-only'}), mock.patch.object(curation, '_post', return_value=response):
        with pytest.raises(AppError) as exc:
            curation.ask('gemini', 'system', 'user', {}, 'test')
    assert exc.value.code == 'curation_bad_response'
