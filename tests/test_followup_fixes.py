"""Regression coverage for the verified phase-4 defects; no external calls."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from unittest import mock

import pytest
from app import config, service, media
from app.errors import AppError


def test_disguised_video_is_rejected_before_fingerprint(tmp_path):
    source = tmp_path / 'text.mp4'
    source.write_text('not a video')
    with mock.patch.object(service, 'quick_fingerprint') as fingerprint:
        with pytest.raises(AppError):
            service.check_path(str(source))
        fingerprint.assert_not_called()


def test_missing_dependency_hash_requires_install(tmp_path):
    # Execute the real hash check from the launcher, with an existing legacy marker.
    text = (config.ROOT / 'iniciar_windows.bat').read_text(encoding='ascii')
    command = next(line.split(' -c "', 1)[1].rsplit('"', 1)[0] for line in text.splitlines()
                   if 'sys.exit(3 if' in line)
    (tmp_path / '.venv').mkdir()
    (tmp_path / '.venv/.deps-ok').touch()
    (tmp_path / 'requirements.txt').write_text('example==1\n')
    env = dict(os.environ, DEPS='-r requirements.txt')
    def run():
        return subprocess.run([sys.executable, '-c', command], cwd=tmp_path, env=env, timeout=10).returncode
    assert run() == 3
    stamp = tmp_path / '.venv/.deps-hash'
    assert not stamp.exists()
    stamp.write_text('old-requirements-only-hash')
    assert run() == 3
    h = hashlib.sha256(env['DEPS'].encode()); h.update((tmp_path / 'requirements.txt').read_bytes())
    stamp.write_text(h.hexdigest())
    assert run() == 0
    (tmp_path / 'requirements.txt').write_text('example==2\n')
    assert run() == 3


@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher')
def test_gpu_selection_requires_success_not_just_path(tmp_path):
    text = (config.ROOT / 'iniciar_windows.bat').read_text(encoding='ascii')
    selection = text[text.index('set "DEPS='):text.index('if not exist .venv\\.deps-ok')]
    (tmp_path / 'requirements-ai.txt').touch(); (tmp_path / 'requirements-gpu.txt').touch()
    script = tmp_path / 'probe.cmd'
    script.write_text('@echo off\n'+selection+'\necho RESULT=%DEPS%\n', encoding='ascii')
    driver = tmp_path / 'nvidia-smi.cmd'
    env = dict(os.environ, PATH=str(tmp_path), AUTOCORTES_MINIMAL='0')
    for rc in (1, 0):
        driver.write_text(f'@exit /b {rc}\n', encoding='ascii')
        result = subprocess.run(['cmd.exe', '/d', '/c', str(script)], env=env, cwd=tmp_path,
                                capture_output=True, text=True, timeout=10)
        assert ('requirements-gpu.txt' in result.stdout) == (rc == 0)
    env['AUTOCORTES_MINIMAL']='1'
    result=subprocess.run(['cmd.exe','/d','/c',str(script)],env=env,cwd=tmp_path,capture_output=True,text=True,timeout=10)
    assert 'RESULT=-r requirements.txt' in result.stdout
