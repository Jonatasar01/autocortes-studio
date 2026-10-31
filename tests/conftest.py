"""Grupo rápido para o dia a dia: ``pytest -m "not lento"``.

São marcados como ``lento`` os testes que criam projetos de verdade (``ServiceBase``) ou geram e renderizam vídeo
com o FFmpeg (``make_video``, ``ffmpeg``): eles respondem pela maior parte dos ~5 min da suíte completa, que continua
sendo a referência antes de cada commit.
"""
from __future__ import annotations

import inspect

import pytest

SLOW_HINTS = ('make_video', "'ffmpeg'", '"ffmpeg"', 'run_ffmpeg', 'export_clip(', 'request_export(')


def pytest_configure(config):
    config.addinivalue_line('markers', 'lento: cria projetos ou renderiza vídeo com o FFmpeg (fora do grupo rápido)')


def _slow(item) -> bool:
    from tests.test_service import ServiceBase
    cls = getattr(item, 'cls', None)
    if cls is not None and issubclass(cls, ServiceBase):
        return True
    try:
        source = inspect.getsource(item.function)
        if cls is not None:
            source += inspect.getsource(cls)
    except (OSError, TypeError):
        return False
    return any(hint in source for hint in SLOW_HINTS)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if _slow(item):
            item.add_marker(pytest.mark.lento)
