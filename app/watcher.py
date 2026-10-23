"""Pasta de entrada monitorada: vídeos colocados em ``entrada/`` são importados sozinhos.

- Só importa arquivos estáveis (tamanho e data inalterados entre verificações), para não pegar
  cópias ainda em andamento.
- Usa as declarações de direitos padrão das Configurações e, se marcado, o piloto automático.
- Detecta duplicatas (mesma impressão digital de conteúdo) e não reprocessa.
- Depois de importar, move o original para ``entrada/processados``; em erro, para ``entrada/falhas``
  com um arquivo ``.erro.txt`` explicando o motivo. Nada é apagado.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path

from . import config, media, settings, storage
from .errors import AppError, sanitize

log = logging.getLogger('autocortes.watcher')
IGNORED_SUFFIXES = ('.part', '.crdownload', '.tmp', '.download', '.partial', '~')

_thread: threading.Thread | None = None
_stop = threading.Event()
_seen: dict[str, tuple[int, float, int]] = {}   # nome -> (tamanho, mtime, verificações estáveis)
_state = {'last_scan': None, 'imported': 0, 'failed': 0, 'duplicates': 0, 'last_error': None, 'running': False}


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return 'sha256:' + digest.hexdigest()


def _duplicate(digest: str, size: int) -> dict | None:
    for item in storage.list_projects():
        saved = item.get('fingerprint')
        if not saved:
            continue
        if not saved.startswith('sha256:'):
            # Projetos antigos usavam um hash parcial. Recalcular o original,
            # nunca comparar o novo arquivo apenas com esse hash legado.
            try:
                p = storage.read_project(item['id'])
                source = storage.project_dir(item['id']) / p['filename']
                if source.stat().st_size != size:
                    continue
                saved = fingerprint(source)
                with storage.edit_project(item['id']) as current:
                    current['source_fingerprint'] = saved
            except (AppError, OSError, KeyError):
                continue
        if saved == digest:
            return item
    return None


def _dirs() -> dict[str, Path]:
    base = config.WATCH_DIR
    return {'base': base, 'processing': base / '.processando', 'done': base / 'processados', 'failed': base / 'falhas'}


def _unique(folder: Path, name: str) -> Path:
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    return folder / f"{stem} ({time.strftime('%Y%m%d-%H%M%S')}){suffix}"


def _candidates(base: Path) -> list[Path]:
    out = []
    for entry in base.iterdir():
        if not entry.is_file() or entry.name.startswith(('.', '~$')):
            continue
        if entry.name.lower().endswith(IGNORED_SUFFIXES):
            continue
        if entry.suffix.lower() in media.ALLOWED_EXTENSIONS:
            out.append(entry)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def scan_once(stable_checks: int = 2) -> list[dict]:
    """Uma varredura. Retorna o que foi feito (para testes e para a interface)."""
    from . import service
    cfg = settings.load()
    if not cfg['watch']['enabled']:
        return []
    dirs = _dirs()
    for folder in dirs.values():
        folder.mkdir(parents=True, exist_ok=True)
    _state['last_scan'] = time.time()
    actions = []
    present = set()
    for path in _candidates(dirs['base']):
        present.add(path.name)
        try:
            stat = path.stat()
        except OSError:
            continue
        size, mtime, count = _seen.get(path.name, (-1, -1.0, 0))
        if (stat.st_size, stat.st_mtime) == (size, mtime) and stat.st_size > 0:
            count += 1
        else:
            count = 0
        _seen[path.name] = (stat.st_size, stat.st_mtime, count)
        if count < stable_checks:
            continue
        claimed = _unique(dirs['processing'], path.name)
        try:
            os.replace(path, claimed)   # falha no Windows se outro programa ainda estiver gravando
        except OSError:
            continue
        _seen.pop(path.name, None)
        actions.append(_import(claimed, path.name, cfg, service, dirs))
    for name in list(_seen):
        if name not in present:
            _seen.pop(name, None)
    return actions


def _import(claimed: Path, original_name: str, cfg: dict, service, dirs: dict[str, Path]) -> dict:
    try:
        digest = fingerprint(claimed)
        duplicate = _duplicate(digest, claimed.stat().st_size)
        if duplicate:
            target = _unique(dirs['done'], original_name)
            os.replace(claimed, target)
            _state['duplicates'] += 1
            log.info('Pasta de entrada: %s já importado (projeto %s).', original_name, duplicate['id'])
            return {'file': original_name, 'status': 'duplicate', 'project_id': duplicate['id']}
        form = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in cfg['upload_defaults'].items()}
        form['autopilot'] = 'true' if cfg['watch']['autopilot'] else 'false'
        with claimed.open('rb') as handle:
            result = service.create_project(iter(lambda: handle.read(4 * 1024 * 1024), b''), original_name, form,
                                            claimed.stat().st_size, origin='pasta_entrada', fingerprint=digest)
        os.replace(claimed, _unique(dirs['done'], original_name))
        _state['imported'] += 1
        log.info('Pasta de entrada: %s importado como projeto %s.', original_name, result['id'])
        return {'file': original_name, 'status': 'imported', 'project_id': result['id']}
    except (AppError, OSError) as exc:
        message = exc.message if isinstance(exc, AppError) else 'Erro ao ler o arquivo: ' + type(exc).__name__
        _state['failed'] += 1
        _state['last_error'] = f'{original_name}: {message}'
        target = _unique(dirs['failed'], original_name)
        try:
            os.replace(claimed, target)
            target.with_name(target.name + '.erro.txt').write_text(
                f'Arquivo: {original_name}\nData: {time.strftime("%d/%m/%Y %H:%M:%S")}\nMotivo: {sanitize(message, 500)}\n'
                'Corrija o problema e coloque o arquivo novamente na pasta de entrada.\n', encoding='utf-8')
        except OSError:
            log.exception('Não foi possível mover %s para falhas', original_name)
        return {'file': original_name, 'status': 'failed', 'error': message}


def _loop() -> None:
    _state['running'] = True
    while not _stop.wait(config.WATCH_INTERVAL):
        try:
            scan_once()
        except Exception as exc:  # o monitor nunca derruba o aplicativo
            _state['last_error'] = sanitize(exc, 200)
            log.exception('Falha na pasta de entrada')
    _state['running'] = False


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    processing = _dirs()['processing']
    if processing.is_dir():  # arquivos presos por encerramento inesperado voltam para a fila
        for leftover in processing.iterdir():
            if leftover.is_file():
                try:
                    os.replace(leftover, _unique(config.WATCH_DIR, leftover.name))
                except OSError:
                    pass
    _stop.clear()
    _thread = threading.Thread(target=_loop, name='watch-folder', daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)


def status() -> dict:
    enabled = settings.load()['watch']['enabled']
    return {'enabled': enabled, 'folder': str(config.WATCH_DIR), **_state}
