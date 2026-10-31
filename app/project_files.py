"""Arquivos do projeto: vídeo duplicado, pastas, cache, nomes de download e exportações apagadas.

Separado do ``service.py`` (os nomes continuam disponíveis lá).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
from pathlib import Path

from . import config, media, publish, service, storage
from .errors import AppError


QUICK_BLOCK = 4 * 1024 * 1024


def quick_fingerprint(path: Path) -> str:
    """Impressão rápida do vídeo: tamanho + SHA-256 de três blocos de 4 MB (início, meio e fim).

    O navegador calcula a mesma coisa antes do envio (``studio.js``, ``quickFingerprint``), então dá para avisar
    que o vídeo já está num projeto sem enviar nem ler 20 GB. Arquivos de até 12 MB entram inteiros.
    """
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        if size <= 3 * QUICK_BLOCK:
            digest.update(handle.read())
        else:
            for offset in (0, size // 2 - QUICK_BLOCK // 2, size - QUICK_BLOCK):
                handle.seek(offset)
                digest.update(handle.read(QUICK_BLOCK))
    return f'quick:{size}:{digest.hexdigest()}'


def find_duplicate(size: int, quick: str) -> dict | None:
    """Projeto que já tem este vídeo (mesmo tamanho e mesma impressão rápida), ou ``None``."""
    if not re.fullmatch(r'quick:\d+:[0-9a-f]{64}', quick or '') or not quick.startswith(f'quick:{int(size)}:'):
        raise AppError('Impressão do vídeo inválida.', 400, 'bad_input')
    for item in storage.list_projects():
        try:
            p = storage.read_project(item['id'])
            source = storage.project_dir(item['id']) / p['filename']
            if source.stat().st_size != int(size):
                continue
            saved = p.get('quick_fingerprint')
            if not saved:
                saved = quick_fingerprint(source)
                with storage.edit_project(item['id']) as current:
                    current['quick_fingerprint'] = saved
        except (AppError, OSError, KeyError):
            continue
        if saved == quick:
            return {'id': item['id'], 'name': item['name'], 'status': item.get('status'), 'clips': item.get('clips'),
                    'size_bytes': int(size)}
    return None


def open_folder(project_id: str | None = None) -> dict:
    """Abre no Explorador a pasta dos arquivos exportados (do projeto, ou a pasta de saída geral).

    Só pastas do próprio app: a pasta de saída do projeto (cópias com nome legível) ou, sem ela, a pasta de
    cortes do projeto. Funciona apenas com o app rodando no computador do usuário (servidor local).
    """
    if project_id:
        p = storage.read_project(project_id)
        folder = publish.output_folder(p)
        if not folder.is_dir():
            folder = storage.project_dir(project_id) / 'clips'
    else:
        folder = config.OUTPUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    _reveal(folder)
    return {'folder': str(folder)}


def _reveal(folder: Path) -> None:
    import subprocess
    import sys
    try:
        if os.name == 'nt':
            os.startfile(str(folder))  # noqa: S606 (pasta do próprio app, servidor só local)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(folder)])
        else:
            subprocess.Popen(['xdg-open', str(folder)])
    except OSError as exc:
        raise AppError(f'Não foi possível abrir a pasta: {folder}', 500, 'open_failed') from exc


def delete_project(project_id: str) -> None:
    storage.project_dir(project_id)
    if service._jobs().for_project(project_id, active_only=True):
        raise AppError('Cancele as tarefas em andamento antes de excluir o projeto.', 409, 'busy')
    storage.delete_project_dir(project_id)


# Caches que o app refaz sozinho quando faltam: miniaturas, onda do áudio, energia, faixas pretas, planos de
# enquadramento e prévias. Vídeo, transcrição, cortes e exportações nunca entram aqui.
CACHE_DIRS = {'cache': 'Miniaturas, onda e medidas do vídeo', 'direction': 'Planos de enquadramento',
              'preview': 'Prévias da edição'}


def _dir_size(folder: Path) -> tuple[int, int]:
    size = count = 0
    for path in folder.rglob('*') if folder.is_dir() else ():
        with contextlib.suppress(OSError):
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
                count += 1
    return size, count


def cache_usage(project_id: str) -> dict:
    folder = storage.project_dir(project_id)
    parts = []
    for name, label in CACHE_DIRS.items():
        size, count = _dir_size(folder / name)
        parts.append({'id': name, 'label': label, 'bytes': size, 'files': count})
    return {'parts': parts, 'total_bytes': sum(x['bytes'] for x in parts)}


def clear_cache(project_id: str) -> dict:
    """Apaga os caches do projeto (refeitos quando necessário). Recusa com tarefa em andamento no projeto."""
    folder = storage.project_dir(project_id)
    if service._jobs().for_project(project_id, active_only=True):
        raise AppError('Espere as tarefas do projeto terminarem (ou cancele) antes de limpar o cache.', 409, 'busy')
    freed = 0
    for name in CACHE_DIRS:
        target = folder / name
        if target.is_symlink() or not target.is_dir():
            continue
        for path in target.iterdir():
            size = _dir_size(path)[0] if path.is_dir() and not path.is_symlink() else 0
            with contextlib.suppress(OSError):
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                    freed += size
                else:
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
    with storage.edit_project(project_id) as p:
        for clip in p.get('clips') or []:   # as prévias apagadas não podem continuar apontadas nos cortes
            clip.pop('preview', None)
    return {'freed_bytes': freed, **cache_usage(project_id)}


# --------------------------------------------------------------------------- arquivos

def source_path(project_id: str) -> tuple[Path, str]:
    p = storage.read_project(project_id)
    path = storage.project_dir(project_id) / p['filename']
    if not path.is_file():
        raise AppError('Vídeo não encontrado.', 404, 'not_found')
    return path, p['name']


def safe_download_name(title: str, suffix: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', title or 'corte').strip(' .')[:80] or 'corte'
    return f'{base}{suffix}'


def export_path(project_id: str, clip_id: int, fmt: str | None, kind: str = 'mp4') -> tuple[Path, str]:
    p = storage.read_project(project_id)
    clip = service._find_clip(p, clip_id)
    exports = clip.get('exports') or {}
    record = exports.get(fmt) if fmt else (clip.get('export_metadata') or None)
    if not record:
        raise AppError('Exporte o trecho neste formato antes de baixar.', 404, 'not_exported')
    name = record.get('file') if kind == 'mp4' else record.get('srt_file')
    if not name or not re.fullmatch(r'clip_\d{2,}_(9x16|16x9|1x1|orig)_v\d+\.(mp4|srt)', name):
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    path = storage.project_dir(project_id) / 'clips' / name
    if not path.is_file():
        raise AppError('Arquivo não encontrado. Exporte novamente.', 404, 'not_found')
    slug = media.FORMAT_SLUG.get(record.get('format'), 'corte')
    return path, safe_download_name(record.get('title') or clip['title'], f'_{slug}.{kind}')


def delete_export(project_id: str, clip_id: int, fmt: str) -> dict:
    """Exclui uma exportação concluída do corte (MP4, legenda e pacote em ``clips/``). As cópias já feitas na pasta
    de saída não são apagadas: são arquivos do usuário fora do projeto."""
    folder = storage.project_dir(project_id)
    busy = [j for j in service._jobs().for_project(project_id, active_only=True)
            if j.get('kind') == 'export' and (j.get('params') or {}).get('clip_id') == clip_id
            and (j.get('params') or {}).get('format') == fmt]
    if busy:
        raise AppError('Este corte está sendo exportado neste formato; espere terminar ou cancele.', 409, 'busy')
    with storage.edit_project(project_id) as p:
        clip = service._find_clip(p, clip_id)
        record = (clip.get('exports') or {}).pop(fmt, None)
        if record is None:
            raise AppError('Exportação não encontrada.', 404, 'not_exported')
        if (clip.get('export_metadata') or {}).get('file') == record.get('file'):
            latest = max((clip.get('exports') or {}).values(), key=lambda r: r.get('created_at') or 0, default=None)
            clip['export_file'] = latest.get('file') if latest else None
            clip['export_metadata'] = latest
            if latest is None:
                clip.pop('export_file', None)
                clip.pop('export_metadata', None)
    freed = 0
    for name in filter(None, (record.get('file'), record.get('srt_file'), record.get('package_file'))):
        path = folder / 'clips' / name
        if path.parent == folder / 'clips' and path.is_file():   # só nomes simples dentro de clips/
            with contextlib.suppress(OSError):
                size = path.stat().st_size
                path.unlink()
                freed += size
    return {'clip_id': clip_id, 'format': fmt, 'freed_bytes': freed,
            'output_kept': bool((record.get('output') or {}).get('files'))}
