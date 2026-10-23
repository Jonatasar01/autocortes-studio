"""iniciar_windows.bat: prefere Python 3.12 e explica com clareza quando ele não está instalado.

Os testes executam o .bat de verdade no cmd.exe, com PATH isolado e um ``py``/``python`` simulados (scripts .bat),
então não dependem do que está instalado na máquina nem baixam nada.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import config

BAT = config.ROOT / 'iniciar_windows.bat'
windows_only = pytest.mark.skipif(os.name != 'nt', reason='exige cmd.exe do Windows')
supported_python = pytest.mark.skipif(not ((3, 10) <= sys.version_info[:2] <= (3, 12)),
                                      reason='cria uma venv real com o Python do teste (3.10 a 3.12)')


# ----------------------------------------------------------------------------- o arquivo em si

def test_launcher_is_ascii_with_crlf_line_endings_only():
    data = BAT.read_bytes()
    data.decode('ascii')  # sem acentos: o cmd usa a página de código do console
    assert data.count(b'\n') == data.count(b'\r\n'), 'LF solto: o cmd erra o goto/rótulos em arquivos só com LF'


def test_launcher_prefers_python_312_and_declares_the_supported_range():
    text = BAT.read_text(encoding='ascii')
    assert 'py -3.12 -m venv .venv' in text
    assert text.count('(3, 10) <= sys.version_info[:2] <= (3, 12)') == 2  # Python do PATH e .venv existente
    assert 'py -3 -m venv' not in text  # o antigo `py -3` escolhia a versão mais nova (3.15 falha no pip)
    assert 'py install 3.12' in text and 'python.org/downloads' in text


# ----------------------------------------------------------------------------- execução real no cmd.exe

PY_STUB = r'''@echo off
echo py %*>> "%STUB_LOG%"
if not "%1"=="-3.12" exit /b 103
if not "%FAKE_312%"=="1" (
  echo No suitable Python runtime found
  exit /b 103
)
"%REAL_PYTHON%" -m venv %4
type nul > "%4\.deps-ok"
exit /b 0
'''

# FAKE_PATH_PYTHON: "ok" = versão suportada; qualquer outro valor = 3.15.1 (não suportada)
PYTHON_STUB = r'''@echo off
echo python %*>> "%STUB_LOG%"
echo %* | findstr /c:"sys.version.split" >nul && (echo 3.15.1& exit /b 0)
echo %* | findstr /c:"sys.exit" >nul && (if "%FAKE_PATH_PYTHON%"=="ok" (exit /b 0) else (exit /b 1))
echo %* | findstr /c:"-m venv" >nul && ("%REAL_PYTHON%" -m venv %3 & type nul > "%3\.deps-ok" & exit /b 0)
exit /b 1
'''


def run_launcher(tmp_path: Path, *, py=False, python=False, fake_312=False, path_python='ok', existing_venv=None):
    project, bin_dir = tmp_path / 'projeto', tmp_path / 'bin'
    project.mkdir()
    bin_dir.mkdir()
    shutil.copy(BAT, project / 'iniciar_windows.bat')
    if py:
        (bin_dir / 'py.bat').write_text(PY_STUB, encoding='ascii')
    if python:
        (bin_dir / 'python.bat').write_text(PYTHON_STUB, encoding='ascii')
    if existing_venv:
        scripts = project / '.venv' / 'Scripts'
        scripts.mkdir(parents=True)
        shutil.copy(existing_venv, scripts / 'python.exe')
    system32 = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'
    log = tmp_path / 'stub.log'
    env = {'PATH': f'{bin_dir};{system32}', 'SystemRoot': str(system32.parent), 'PATHEXT': '.COM;.EXE;.BAT;.CMD',
           'AUTOCORTES_NO_BROWSER': '1', 'STUB_LOG': str(log), 'REAL_PYTHON': sys.executable,
           'FAKE_312': '1' if fake_312 else '0', 'FAKE_PATH_PYTHON': path_python, 'TEMP': str(tmp_path)}
    result = subprocess.run(['cmd', '/c', str(project / 'iniciar_windows.bat')], cwd=project, env=env,
                            capture_output=True, text=True, errors='replace', timeout=120)
    calls = log.read_text(encoding='ascii', errors='replace').splitlines() if log.exists() else []
    return result, project, calls


@windows_only
@supported_python
def test_creates_the_venv_with_py_3_12_when_available(tmp_path):
    result, project, calls = run_launcher(tmp_path, py=True, fake_312=True)
    assert 'py -3.12 -m venv .venv' in calls
    assert (project / '.venv' / 'Scripts' / 'python.exe').is_file()
    assert 'ERRO' not in result.stdout  # o uvicorn final falha (sem pacotes), mas sem mensagem de Python


@windows_only
def test_py_without_3_12_and_no_python_explains_how_to_install_it(tmp_path):
    result, project, calls = run_launcher(tmp_path, py=True, fake_312=False)
    assert result.returncode == 1
    assert 'py -3.12 -m venv .venv' in calls
    assert 'ERRO: Python 3.12 nao encontrado.' in result.stdout
    assert 'py install 3.12' in result.stdout and 'python.org/downloads' in result.stdout
    assert not (project / '.venv').exists()


@windows_only
def test_no_py_and_no_python_explains_how_to_install_it(tmp_path):
    result, project, calls = run_launcher(tmp_path)
    assert result.returncode == 1 and calls == []
    assert 'ERRO: Python 3.12 nao encontrado.' in result.stdout and 'py install 3.12' in result.stdout


@windows_only
def test_unsupported_python_on_path_is_rejected_with_its_version(tmp_path):
    result, project, calls = run_launcher(tmp_path, py=True, fake_312=False, python=True, path_python='3.15')
    assert result.returncode == 1
    assert 'versao 3.15.1, que nao e suportada' in result.stdout
    assert 'ERRO: Python 3.12 nao encontrado.' in result.stdout and 'py install 3.12' in result.stdout
    assert not any('-m venv .venv' in c and c.startswith('python') for c in calls)  # nunca cria a venv com ele
    assert not (project / '.venv').exists()


@windows_only
@supported_python
def test_supported_python_on_path_is_used_when_py_is_missing(tmp_path):
    result, project, calls = run_launcher(tmp_path, python=True, path_python='ok')
    assert any(c.startswith('python -m venv') for c in calls)
    assert (project / '.venv' / 'Scripts' / 'python.exe').is_file()
    assert 'ERRO' not in result.stdout


@windows_only
def test_existing_venv_with_unsupported_python_is_reported_and_kept(tmp_path):
    # where.exe copiado como python.exe: o teste de versão (-c ...) falha, como numa .venv antiga de outra versão
    fake_python = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'where.exe'
    result, project, calls = run_launcher(tmp_path, py=True, fake_312=True, existing_venv=fake_python)
    assert result.returncode == 1
    assert 'a pasta .venv deste projeto foi criada com uma versao de Python nao suportada' in result.stdout
    assert 'Apague a pasta .venv' in result.stdout and 'py install 3.12' in result.stdout
    assert (project / '.venv' / 'Scripts' / 'python.exe').is_file()  # nunca apaga nada do usuário
    assert calls == []  # nem tenta criar outra venv por cima
