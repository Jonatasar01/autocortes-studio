"""Dados visuais da linha do tempo do editor: tira de miniaturas e forma de onda, gerados uma vez e em cache.

- Miniaturas: o navegador buscava ~40 pontos do vídeo original com um ``<video>`` oculto; num arquivo de vários
  GB cada busca podia passar de 6 s e a faixa ficava escura. Aqui o FFmpeg lê um quadro por posição (busca
  direta pelo índice do arquivo) e monta uma tira JPEG única.
- Forma de onda: picos do áudio por janela de tempo, medidos pelo próprio FFmpeg (``astats``), sem numpy.
  A primeira geração roda em segundo plano (minutos numa live de horas); a interface consulta até ficar pronta.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import media, storage
from .errors import AppError

log = logging.getLogger('autocortes.timeline')

THUMB_W, THUMB_H = 160, 90
MAX_FRAMES = 60
WAVE_BUCKET = 0.25           # segundos por pico em vídeos de até 2 h
WAVE_MAX_BUCKETS = 40_000    # acima disso a janela cresce (live de 8 h: ~0,76 s por pico)
WAVE_RATE = 8000
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_waves_running: set[str] = set()


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _source(project_id: str) -> tuple[Path, dict, Path]:
    p = storage.read_project(project_id)
    if p.get('status') not in ('ready', 'analyzing', 'queued') or not p.get('media'):
        raise AppError('O vídeo ainda não está pronto.', 409, 'not_ready')
    folder = storage.project_dir(project_id)
    return folder / p['filename'], p['media'], folder / 'cache'


def filmstrip(project_id: str, count: int) -> Path:
    """Tira JPEG com ``count`` quadros (160×90) igualmente espaçados; gerada uma vez por quantidade."""
    count = max(4, min(MAX_FRAMES, int(count)))
    source, info, cache = _source(project_id)
    out = cache / f'filmstrip_{count}.jpg'
    if out.is_file():
        return out
    with _lock(f'{project_id}:film:{count}'):
        if out.is_file():
            return out
        cache.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix='.film-', dir=cache))
        try:
            duration = info['duration']
            gap = duration / count
            scale = (f'scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase,'
                     f'crop={THUMB_W}:{THUMB_H}')

            def grab(i: int) -> None:
                t = min(duration - 0.1, (i + 0.5) * gap)
                media.run_ffmpeg(['-ss', f'{max(0.0, t):.3f}', '-i', str(source), '-map', f"0:{info['video_index']}",
                                  '-frames:v', '1', '-vf', scale, '-q:v', '5', str(work / f'f{i:03d}.jpg')],
                                 duration=1.0)

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(grab, range(count)))
            missing = [i for i in range(count) if not (work / f'f{i:03d}.jpg').is_file()]
            for i in missing:   # quadro ilegível (fim do arquivo, erro pontual): repete o vizinho
                neighbour = next((work / f'f{j:03d}.jpg' for j in range(i - 1, -1, -1)
                                  if (work / f'f{j:03d}.jpg').is_file()), None)
                if neighbour:
                    shutil.copyfile(neighbour, work / f'f{i:03d}.jpg')
            media.run_ffmpeg(['-i', str(work / 'f%03d.jpg'), '-vf', f'tile={count}x1', '-frames:v', '1',
                              '-q:v', '5', str(work / 'tira.jpg')], duration=1.0)
            (work / 'tira.jpg').replace(out)
        except media.FFmpegError as exc:
            raise AppError('Não foi possível gerar as miniaturas.', 500, 'filmstrip_failed') from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return out


def waveform(project_id: str) -> dict:
    """``{'state': 'ready', 'bucket': s, 'peaks': base64 de bytes 0–255}`` ou ``{'state': 'pending'}``."""
    source, info, cache = _source(project_id)
    out = cache / 'waveform.json'
    if out.is_file():
        return {'state': 'ready', **json.loads(out.read_text(encoding='utf-8'))}
    if info.get('audio_index') is None:
        return {'state': 'none', 'message': 'Vídeo sem áudio.'}
    with _locks_guard:
        if project_id not in _waves_running:
            _waves_running.add(project_id)
            threading.Thread(target=_build_wave, args=(project_id, source, info, cache, out), daemon=True,
                             name=f'onda-{project_id[:8]}').start()
    return {'state': 'pending'}


def _build_wave(project_id: str, source: Path, info: dict, cache: Path, out: Path) -> None:
    try:
        duration = info['duration']
        bucket = max(WAVE_BUCKET, duration / WAVE_MAX_BUCKETS)
        samples = max(1, round(bucket * WAVE_RATE))
        cache.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix='.onda-', dir=cache))
        try:
            levels = work / 'picos.txt'
            # aresample dentro do filtro: o -ar de saída só valeria depois do astats (janelas 6x menores).
            media.run_ffmpeg(['-i', str(source), '-map', f"0:{info['audio_index']}", '-vn', '-ac', '1', '-af',
                              f'aresample={WAVE_RATE},asetnsamples=n={samples}:p=0,astats=metadata=1:reset=1,'
                              f'ametadata=print:key=lavfi.astats.Overall.Peak_level:file=picos.txt',
                              '-f', 'null', '-'], duration=duration, cwd=work)
            peaks = bytearray()
            for match in re.finditer(r'Peak_level=(-?[\d.]+|-inf)', levels.read_text(errors='replace')):
                db = -60.0 if match.group(1) == '-inf' else max(-60.0, min(0.0, float(match.group(1))))
                peaks.append(round((db + 60.0) / 60.0 * 255))
            data = {'bucket': round(bucket, 4), 'count': len(peaks),
                    'peaks': base64.b64encode(bytes(peaks)).decode('ascii')}
            tmp = cache / '.waveform.tmp'
            tmp.write_text(json.dumps(data), encoding='utf-8')
            tmp.replace(out)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    except Exception as exc:  # sem forma de onda a linha do tempo continua funcionando
        log.warning('Forma de onda de %s não gerada: %s', project_id, exc)
    finally:
        with _locks_guard:
            _waves_running.discard(project_id)
