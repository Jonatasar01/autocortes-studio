"""Persistência de projetos em disco.

- project.json gravado de forma atômica (arquivo temporário + fsync + os.replace) com cópia .bak.
- Bloqueio por projeto: leituras/alterações concorrentes (exportações, edições) não se sobrescrevem.
- Identificadores validados por regex — sem path traversal.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import config
from .errors import AppError

log = logging.getLogger('autocortes.storage')
ID_RE = re.compile(r'^[0-9a-f]{32}$')
_registry = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def data_dir() -> Path:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR


def lock_for(project_id: str) -> threading.RLock:
    with _registry:
        return _locks.setdefault(project_id, threading.RLock())


def valid_id(project_id: str) -> bool:
    return bool(ID_RE.fullmatch(project_id or ''))


def project_dir(project_id: str) -> Path:
    if not valid_id(project_id):
        raise AppError('Projeto não encontrado.', 404, 'not_found')
    folder = data_dir() / project_id
    if not folder.is_dir():
        raise AppError('Projeto não encontrado.', 404, 'not_found')
    return folder


def new_project_dir() -> tuple[str, Path]:
    project_id = uuid.uuid4().hex
    folder = data_dir() / project_id
    folder.mkdir(parents=True)
    return project_id, folder


def read_project(project_id: str) -> dict:
    folder = project_dir(project_id)
    with lock_for(project_id):
        for name in ('project.json', 'project.json.bak'):
            path = folder / name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    if name.endswith('.bak'):
                        log.warning('project.json ilegível em %s; usando cópia de segurança.', project_id)
                        # Reconcilia o principal sem mover/apagar o único backup válido.
                        repair = None
                        try:
                            fd, repair = tempfile.mkstemp(prefix='.project-', suffix='.tmp', dir=folder)
                            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                                json.dump(data, handle, ensure_ascii=False, indent=2)
                                handle.flush()
                                os.fsync(handle.fileno())
                            os.replace(repair, folder / 'project.json')
                        except OSError:
                            log.warning('Não foi possível restaurar project.json em %s.', project_id)
                        finally:
                            if repair is not None:
                                with contextlib.suppress(OSError):
                                    os.unlink(repair)
                    return data
            except (OSError, json.JSONDecodeError):
                continue
    raise AppError('Projeto ainda não disponível ou corrompido.', 404, 'not_found')


def write_project(project_id: str, data: dict) -> None:
    folder = project_dir(project_id)
    with lock_for(project_id):
        data['updated_at'] = time.time()
        fd, tmp_name = tempfile.mkstemp(prefix='.project-', suffix='.tmp', dir=folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            current = folder / 'project.json'
            if current.exists():
                os.replace(current, folder / 'project.json.bak')
            os.replace(tmp_name, current)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


@contextlib.contextmanager
def edit_project(project_id: str):
    """Leitura-alteração-gravação atômica em relação a outras threads."""
    with lock_for(project_id):
        data = read_project(project_id)
        yield data
        write_project(project_id, data)


def list_projects() -> list[dict]:
    items = []
    for folder in data_dir().iterdir():
        if not (folder.is_dir() and valid_id(folder.name) and
                any((folder / name).exists() for name in ('project.json', 'project.json.bak'))):
            continue
        try:
            p = read_project(folder.name)
        except AppError:
            continue
        items.append({'id': p.get('id'), 'name': p.get('name'), 'status': p.get('status'),
                      'created_at': p.get('created_at'), 'duration': (p.get('media') or {}).get('duration'),
                      'clips': len(p.get('clips') or []), 'origin': p.get('origin', 'upload'),
                      'autopilot': (p.get('autopilot') or {}).get('status'),
                      'fingerprint': p.get('source_fingerprint')})
    return sorted(items, key=lambda x: x.get('created_at') or 0, reverse=True)


def delete_project_dir(project_id: str) -> None:
    folder = project_dir(project_id)
    with lock_for(project_id):
        shutil.rmtree(folder, ignore_errors=True)
    with _registry:
        _locks.pop(project_id, None)


def free_bytes(path: Path | None = None) -> int:
    return shutil.disk_usage(path or data_dir()).free


def ensure_free_space(needed_bytes: int, path: Path | None = None) -> None:
    free = free_bytes(path)
    if free < needed_bytes + config.MIN_FREE_BYTES:
        raise AppError(f'Espaço em disco insuficiente: {free / 1024 ** 3:.1f} GB livres; são necessários cerca de '
                       f'{(needed_bytes + config.MIN_FREE_BYTES) / 1024 ** 3:.1f} GB.', 507, 'disk_full')


def cleanup_stale_files(max_age_seconds: int = 3600) -> int:
    """Na inicialização: remove temporários e uploads incompletos (sem project.json)."""
    removed = 0
    now = time.time()
    for folder in data_dir().iterdir():
        if not (folder.is_dir() and valid_id(folder.name)):
            continue
        for tmp in [folder / 'clips' / '.tmp', folder / 'work']:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
                removed += 1
        for part in folder.glob('*.part'):
            with contextlib.suppress(OSError):
                part.unlink()
                removed += 1
        for tmp in folder.glob('.project-*.tmp'):
            with contextlib.suppress(OSError):
                tmp.unlink()
        if not (folder / 'project.json').exists() and not (folder / 'project.json.bak').exists():
            if now - folder.stat().st_mtime > max_age_seconds:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
    return removed
