"""Missing isolation/race cases: no model downloads, GPU allocation or network paths opened."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest
from app import config, service, transcription, media
from app.errors import AppError


@pytest.fixture
def models(monkeypatch):
    built=[]
    def construct(*args, **kwargs):
        time.sleep(.02)  # Releases the GIL: a missing lock creates duplicate models.
        model=object();built.append((args,kwargs,model));return model
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=construct))
    monkeypatch.setattr(transcription,'_cache',{})
    monkeypatch.setattr(transcription,'_refine_cache',{})
    monkeypatch.setattr(transcription,'_cuda_dll_dirs',lambda:None)
    monkeypatch.setattr(config,'WHISPER_MODEL','auto')
    monkeypatch.setattr(config,'WHISPER_REFINE_MODEL',transcription.GPU_MODEL)
    return built


def test_parallel_analysis_and_refinement_load_one_model(models):
    gate=threading.Barrier(8)
    def load(i):
        gate.wait(timeout=5)
        return transcription._load_model('cuda') if i%2 else transcription._load_refine_model()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(load,range(8)))
    assert len(models)==1
    assert all(model is results[0] for model in results)


def test_cpu_is_not_shared_with_gpu_even_for_same_name(models,monkeypatch):
    monkeypatch.setattr(config,'WHISPER_MODEL',transcription.GPU_MODEL)
    assert transcription._load_model('cpu') is not transcription._load_refine_model()
    assert len(models)==2
    assert {x[1]['compute_type'] for x in models}=={'int8','int8_float16'}


def test_gpu_different_compute_type_is_not_shared(models,monkeypatch):
    monkeypatch.setattr(config,'WHISPER_MODEL','small')
    monkeypatch.setattr(config,'WHISPER_REFINE_MODEL','small')
    assert transcription._load_model('cuda') is not transcription._load_refine_model()
    assert len(models)==2


@pytest.mark.parametrize('path',[
    r'\\server\share\movie.mp4',r'\\?\C:\movie.mp4',r'\\.\C:\movie.mp4',
    '//server/share/movie.mp4',r'C:\movie.mp4:hidden',r'C:\folder:stream\movie.mp4',
])
def test_special_paths_are_rejected_without_filesystem_or_network_access(path):
    with mock.patch.object(Path,'is_file',side_effect=AssertionError('filesystem touched')):
        with pytest.raises(AppError) as error: service.check_path(path)
    assert error.value.code=='bad_path'


def test_symlink_target_extension_is_validated(tmp_path):
    link=tmp_path/'link.mp4';link.touch()
    target=tmp_path/'secret.txt';target.write_text('synthetic')
    with mock.patch.object(Path,'resolve',return_value=target), mock.patch.object(media,'probe_media') as probe:
        with pytest.raises(AppError) as error: service.check_path(str(link))
    assert error.value.code=='bad_path';probe.assert_not_called()


def test_symlink_to_real_video_uses_canonical_target(tmp_path):
    link=tmp_path/'link.mp4';link.touch()
    target=tmp_path/'actual.mp4';target.touch()
    with mock.patch.object(Path,'resolve',return_value=target), mock.patch.object(media,'probe_media') as probe:
        assert service._local_video(str(link))==target
    probe.assert_called_once_with(target)
