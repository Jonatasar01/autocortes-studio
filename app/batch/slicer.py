"""Corte bruto: extrai o trecho do original sem recodificar (``-c copy``).

O stream copy só pode começar num quadro-chave, que costuma ficar alguns segundos
antes do início pedido. Versões do FFmpeg tratam essa sobra de jeitos diferentes
(algumas a escondem com edit list, outras não), então nenhum cálculo a partir de
metadados é confiável em todas elas. Por isso o início é localizado pelo conteúdo:

1. tira-se a impressão digital (MD5 de uma miniatura) do quadro pedido no original;
2. procura-se esse mesmo quadro no arquivo temporário, que é a cópia bit a bit;
3. ``offset`` é o tempo desse quadro no temporário, no mesmo relógio que a
   renderização usa para buscar (``-ss offset -i temporário``).

Se o quadro não for encontrado (decodificação divergente), usa a estimativa pelo
quadro-chave. Quadros idênticos repetidos (tela parada) não importam: escolhe-se o
candidato mais próximo da estimativa.
"""
from __future__ import annotations

import json
import math
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import media
from ..errors import AppError, Cancelled

KEYFRAME_WINDOW = 30.0          # janela inicial para achar o quadro-chave anterior
FINGERPRINT = 'scale=96:54:flags=area,format=gray'


@dataclass
class SliceResult:
    path: Path
    offset: float     # posição do início pedido dentro do arquivo temporário (segundos)
    duration: float   # duração pedida (tempo_fim - tempo_inicio)
    info: dict        # probe_media do arquivo temporário


def _probe(args: list[str]) -> dict:
    try:
        proc = media.run([media.require_binary('ffprobe'), '-v', 'error', *args, '-of', 'json'], timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise AppError('Não foi possível ler os tempos do vídeo com o ffprobe.', 422, 'probe_failed') from exc
    return json.loads(proc.stdout or '{}')


def _format_start(path: Path) -> float:
    try:
        return max(0.0, float(_probe(['-show_entries', 'format=start_time', str(path)])
                              .get('format', {}).get('start_time') or 0))
    except (TypeError, ValueError):
        return 0.0


def _frame_time(frame: dict) -> float | None:
    try:
        return float(frame.get('pts_time') or frame.get('best_effort_timestamp_time'))
    except (TypeError, ValueError):
        return None


def keyframe_before(source: Path, t: float) -> float:
    """Tempo (relógio do arquivo) do último quadro-chave de vídeo com instante <= ``t``."""
    window = KEYFRAME_WINDOW
    while True:
        a = max(0.0, t - window)
        frames = _probe(['-select_streams', 'v:0', '-skip_frame', 'nokey',
                         '-show_entries', 'frame=pts_time,best_effort_timestamp_time',
                         '-read_intervals', f'{a:.3f}%{t + 0.001:.3f}', str(source)]).get('frames', [])
        times = [x for x in map(_frame_time, frames) if x is not None and x <= t + 1e-3]
        if times:
            return max(times)
        if a == 0.0:
            return 0.0
        window *= 4


def _fingerprints(args: list[str]) -> list[tuple[float, str]]:
    """(tempo, md5) de cada quadro decodificado, com os tempos do demuxer (-copyts)."""
    try:
        proc = media.run([media.require_binary('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin',
                          '-copyts', *args, '-map', '0:v:0', '-vf', FINGERPRINT, '-f', 'framemd5', '-'],
                         timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    timebase, out = None, []
    for line in proc.stdout.splitlines():
        if line.startswith('#tb 0:'):
            num, den = line.split(':', 1)[1].strip().split('/')
            timebase = int(num) / int(den)
        elif line and not line.startswith('#') and timebase:
            parts = [p.strip() for p in line.split(',')]
            out.append((int(parts[2]) * timebase, parts[5]))
    return out


def locate(source: Path, start: float, clip: Path, estimate: float, window: float, fps: float) -> float:
    """Tempo, no relógio do demuxer de ``clip``, do quadro igual ao de ``start`` no original."""
    target = _fingerprints(['-ss', f'{start:.6f}', '-i', str(source), '-frames:v', '1'])
    frames = math.ceil((window + 1.5) * max(fps, 1)) + 8
    candidates = _fingerprints(['-i', str(clip), '-frames:v', str(frames)])
    if target:
        matches = [t for t, h in candidates if h == target[0][1]]
        if matches:
            return min(matches, key=lambda t: abs(t - estimate))
    return estimate


def slice_copy(source: Path, start: float, end: float, dest: Path, *,
               cancel_event: threading.Event | None = None) -> SliceResult:
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()
    dest.parent.mkdir(parents=True, exist_ok=True)
    origin = _format_start(source)
    key = keyframe_before(source, start + origin) - origin
    # -ss/-to antes de -i: busca rápida pelo índice, sem decodificar o original.
    media.run_ffmpeg(['-ss', f'{start:.3f}', '-to', f'{end:.3f}', '-i', str(source),
                      '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-sn', '-dn',
                      '-map_metadata', '-1', str(dest)],
                     duration=end - start, cancel_event=cancel_event)
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()
    info = media.probe_media(dest)
    clip_start = _format_start(dest)
    # Estimativa: o temporário começa no quadro-chave (versões que não escondem a sobra)
    # ou já no início pedido (versões que escondem). Fica com a mais próxima do conteúdo.
    first = _fingerprints(['-i', str(dest), '-frames:v', '1'])
    first_time = first[0][0] if first else clip_start
    estimate = first_time + (start - key) if first_time - clip_start < 1e-3 else first_time
    found = locate(source, start, dest, estimate, start - key, info.get('fps') or 30)
    return SliceResult(path=dest, offset=round(max(0.0, found - clip_start), 6),
                       duration=round(end - start, 3), info=info)
