"""Corte bruto: extrai o trecho do original sem recodificar (``-c copy``).

O stream copy só pode começar num quadro-chave, que costuma ficar alguns segundos
antes do início pedido. Para não levar esse erro ao vídeo final, a cópia preserva os
tempos do original (``-copyts``) e o módulo devolve ``offset``: onde o início pedido
está dentro do arquivo temporário. A renderização busca exatamente esse ponto, então o
corte final tem precisão de quadro e o temporário continua barato de gerar.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from .. import media
from ..errors import Cancelled


@dataclass
class SliceResult:
    path: Path
    offset: float     # posição do início pedido dentro do arquivo temporário (segundos)
    duration: float   # duração pedida (tempo_fim - tempo_inicio)
    info: dict        # probe_media do arquivo temporário


def _format_start(path: Path) -> float:
    try:
        return max(0.0, float(media._ffprobe_json(path).get('format', {}).get('start_time') or 0))
    except (TypeError, ValueError):
        return 0.0


def slice_copy(source: Path, start: float, end: float, dest: Path, *,
               cancel_event: threading.Event | None = None) -> SliceResult:
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # -ss/-to antes de -i: busca rápida pelo índice, sem decodificar.
    # -copyts: o temporário mantém o relógio do original, então o início pedido
    # continua localizável mesmo que a cópia comece no quadro-chave anterior.
    media.run_ffmpeg(['-ss', f'{start:.3f}', '-to', f'{end:.3f}', '-copyts', '-i', str(source),
                      '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy', '-sn', '-dn',
                      '-map_metadata', '-1', str(dest)],
                     duration=end - start, cancel_event=cancel_event)
    # Tempos do CSV contam do início do arquivo; o ffmpeg busca relativo ao start_time.
    offset = start + _format_start(source) - _format_start(dest)
    return SliceResult(path=dest, offset=round(max(0.0, offset), 6), duration=round(end - start, 3),
                       info=media.probe_media(dest))
