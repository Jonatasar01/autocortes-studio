"""Persistência de projetos em disco.

- project.json gravado de forma atômica (arquivo temporário + fsync + os.replace) com cópia .bak.
- A transcrição fica em transcript.json, separada: numa live longa ela tem vários MB e o resto do projeto
  poucos KB. Ela só é regravada quando é substituída, e fica em memória enquanto o arquivo não muda; assim
  cada edição de corte grava ~40 KB em vez de reescrever a transcrição inteira.
- Bloqueio por projeto: leituras/alterações concorrentes (exportações, edições) não se sobrescrevem.
- Identificadores validados por regex — sem path traversal.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
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


def _dumps(data: dict) -> str:
    """JSON compacto: com ``indent`` o Python não usa o codificador em C e um projeto de live longa (transcrição
    de vários MB) levava quase 1 s para gravar a cada edição; compacto, 0,2 s e metade do tamanho."""
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


TRANSCRIPT_FILE = 'transcript.json'
_transcripts: dict[str, tuple[tuple, list]] = {}   # projeto -> (carimbo do arquivo, transcrição já lida)


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix='.' + path.stem + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _stamp(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _load_transcript(project_id: str, folder: Path) -> list | None:
    path = folder / TRANSCRIPT_FILE
    stamp = _stamp(path)
    if stamp is None:
        return None
    cached = _transcripts.get(project_id)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        log.warning('transcript.json ilegível em %s.', project_id)
        return None
    if not isinstance(data, list):
        return None
    _transcripts[project_id] = (stamp, data)
    return data


def _save_transcript(project_id: str, folder: Path, data: dict) -> None:
    """Grava a transcrição só se ela foi trocada (as edições comuns não mexem nela)."""
    path = folder / TRANSCRIPT_FILE
    if 'transcript' not in data:
        with contextlib.suppress(OSError):
            path.unlink()
        _transcripts.pop(project_id, None)
        return
    transcript = data['transcript']
    cached = _transcripts.get(project_id)
    if cached and cached[1] is transcript and cached[0] == _stamp(path):
        return
    _write_atomic(path, _dumps(transcript))
    _transcripts[project_id] = (_stamp(path), transcript)


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
                                handle.write(_dumps(data))
                                handle.flush()
                                os.fsync(handle.fileno())
                            os.replace(repair, folder / 'project.json')
                        except OSError:
                            log.warning('Não foi possível restaurar project.json em %s.', project_id)
                        finally:
                            if repair is not None:
                                with contextlib.suppress(OSError):
                                    os.unlink(repair)
                    if 'transcript' not in data:
                        transcript = _load_transcript(project_id, folder)
                        if transcript is not None:
                            data['transcript'] = transcript
                    return data
            except (OSError, json.JSONDecodeError):
                continue
    raise AppError('Projeto ainda não disponível ou corrompido.', 404, 'not_found')


def write_project(project_id: str, data: dict) -> None:
    folder = project_dir(project_id)
    with lock_for(project_id):
        data['updated_at'] = time.time()
        _save_transcript(project_id, folder, data)   # antes: o project.json novo nunca aponta para nada velho
        body = {k: v for k, v in data.items() if k != 'transcript'}
        fd, tmp_name = tempfile.mkstemp(prefix='.project-', suffix='.tmp', dir=folder)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(_dumps(body))
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


_summaries: dict[str, tuple[tuple, dict]] = {}   # resumo da lista por projeto, válido enquanto o arquivo não muda
_summaries_lock = threading.Lock()


def _summary(project_id: str, folder: Path) -> dict | None:
    try:
        st = (folder / 'project.json').stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    with _summaries_lock:
        cached = _summaries.get(project_id)
    if stamp is not None and cached and cached[0] == stamp:
        return cached[1]
    try:
        p = read_project(project_id)
    except AppError:
        return None
    item = {'id': p.get('id'), 'name': p.get('name'), 'status': p.get('status'),
            'created_at': p.get('created_at'), 'duration': (p.get('media') or {}).get('duration'),
            'clips': len(p.get('clips') or []), 'origin': p.get('origin', 'upload'),
            'autopilot': (p.get('autopilot') or {}).get('status'),
            'fingerprint': p.get('source_fingerprint')}
    if stamp is not None:
        with _summaries_lock:
            _summaries[project_id] = (stamp, item)
    return item


def list_projects() -> list[dict]:
    items = []
    for folder in data_dir().iterdir():
        if not (folder.is_dir() and valid_id(folder.name) and
                any((folder / name).exists() for name in ('project.json', 'project.json.bak'))):
            continue
        item = _summary(folder.name, folder)
        if item is not None:
            items.append(dict(item))
    return sorted(items, key=lambda x: x.get('created_at') or 0, reverse=True)


TRASH_PREFIX = '.excluindo-'


def delete_project_dir(project_id: str) -> None:
    """Exclui o projeto de forma atômica: ou some inteiro, ou fica intacto.

    A pasta é renomeada antes de ser apagada. No Windows, um arquivo aberto (vídeo em
    reprodução, exportação lida por outro programa) impede a renomeação; nesse caso nada
    é alterado e o usuário recebe uma mensagem clara, em vez de um projeto pela metade.
    """
    folder = project_dir(project_id)
    trash = folder.with_name(f'{TRASH_PREFIX}{project_id}-{secrets.token_hex(4)}')
    with lock_for(project_id):
        for attempt in range(8):
            try:
                folder.rename(trash)
                break
            except PermissionError:
                if attempt == 7:
                    raise AppError('Algum arquivo do projeto está em uso (vídeo aberto em outro programa?). '
                                   'Feche-o e tente excluir de novo.', 409, 'in_use') from None
                time.sleep(0.15 * (attempt + 1))
    with _registry:
        _locks.pop(project_id, None)
    _remove_tree(trash)


def _remove_tree(path: Path) -> None:
    # O restante (se algo ainda estiver travado) é removido na próxima inicialização.
    for attempt in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.1 * (attempt + 1))


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
        if folder.is_dir() and folder.name.startswith(TRASH_PREFIX):
            shutil.rmtree(folder, ignore_errors=True)  # exclusão interrompida
            removed += 1
            continue
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
