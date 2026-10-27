"""Filtros de cor (looks) aplicados no vídeo exportado, com amostras de como ficam.

Cada filtro tem a cadeia do FFmpeg (exata: exportação, prévia e amostras) e uma aproximação em CSS para o
player mostrar o efeito na hora, enquanto o vídeo toca. As amostras são um quadro do corte com cada filtro,
lado a lado numa tira JPEG gerada uma vez por instante (cache).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import media, storage
from .errors import AppError

# id -> rótulo, descrição, cadeia do FFmpeg (sem vírgula inicial) e aproximação CSS
FILTERS: dict[str, dict] = {
    'nenhum': {'label': 'Original', 'desc': 'Sem filtro.', 'ffmpeg': '', 'css': ''},
    'vivido': {'label': 'Vívido', 'desc': 'Cores fortes e contraste: games e reações.',
               'ffmpeg': 'eq=contrast=1.10:saturation=1.40,unsharp=5:5:0.4',
               'css': 'saturate(1.4) contrast(1.1)'},
    'quente': {'label': 'Quente', 'desc': 'Tons dourados, pele mais viva.',
               'ffmpeg': 'colorbalance=rs=0.06:gs=0.02:bs=-0.06:rm=0.08:gm=0.02:bm=-0.08,eq=saturation=1.10',
               'css': 'sepia(0.25) saturate(1.2) hue-rotate(-8deg)'},
    'frio': {'label': 'Frio', 'desc': 'Azulado e limpo, clima noturno.',
             'ffmpeg': 'colorbalance=rs=-0.05:bs=0.08:rm=-0.04:bm=0.06,eq=saturation=0.95',
             'css': 'saturate(0.95) hue-rotate(12deg) brightness(1.02)'},
    'cinema': {'label': 'Cinema', 'desc': 'Sombras azuladas, luzes alaranjadas e vinheta.',
               'ffmpeg': 'colorbalance=rs=-0.06:bs=0.08:rh=0.08:bh=-0.06,eq=contrast=1.10:saturation=0.92,'
                         'vignette=angle=PI/5',
               'css': 'contrast(1.12) saturate(0.9) sepia(0.12)'},
    'neon': {'label': 'Neon', 'desc': 'Saturação alta e pretos fundos: setup RGB, gameplay.',
             'ffmpeg': 'eq=contrast=1.18:saturation=1.65:gamma=0.95,unsharp=5:5:0.5',
             'css': 'saturate(1.7) contrast(1.2) brightness(0.97)'},
    'vintage': {'label': 'Vintage', 'desc': 'Cores desbotadas e quentes de filme antigo.',
                'ffmpeg': 'curves=preset=vintage,vignette=angle=PI/4',
                'css': 'sepia(0.45) contrast(0.95) saturate(0.85)'},
    'suave': {'label': 'Suave', 'desc': 'Pretos levantados e menos saturação: vlog, conversa.',
              'ffmpeg': "curves=all='0/0.07 0.5/0.52 1/0.96',eq=saturation=0.85",
              'css': 'contrast(0.88) saturate(0.85) brightness(1.04)'},
    'pb': {'label': 'Preto e branco', 'desc': 'Clássico, com contraste.',
           'ffmpeg': 'hue=s=0,eq=contrast=1.15', 'css': 'grayscale(1) contrast(1.15)'},
}
SAMPLE_H = 240


def chain(filter_id: str | None) -> str:
    """Cadeia do FFmpeg do filtro, com vírgula inicial (para somar ao ``post``), ou vazia."""
    if not filter_id or filter_id == 'nenhum':
        return ''
    if filter_id not in FILTERS:
        raise AppError('Filtro inválido.', 400, 'bad_filter')
    return ',' + FILTERS[filter_id]['ffmpeg']


def catalog() -> list[dict]:
    return [{'id': k, 'label': v['label'], 'desc': v['desc'], 'css': v['css']} for k, v in FILTERS.items()]


def sample_strip(project_id: str, t: float) -> Path:
    """Tira JPEG com o quadro do instante ``t`` em cada filtro (ordem de ``FILTERS``), em cache por instante."""
    p = storage.read_project(project_id)
    info = p.get('media')
    if p.get('status') != 'ready' or not info:
        raise AppError('O vídeo ainda não está pronto.', 409, 'not_ready')
    folder = storage.project_dir(project_id)
    t = max(0.0, min(float(t), info['duration'] - 0.1))
    cache = folder / 'cache'
    out = cache / f'filtros_{int(t * 10)}.jpg'
    if out.is_file():
        return out
    cache.mkdir(parents=True, exist_ok=True)
    ids = list(FILTERS)
    labels = ''.join(f'[f{k}]' for k in range(len(ids)))
    parts = [f"[0:{info['video_index']}]scale=-2:{SAMPLE_H}:flags=bicubic,setsar=1,split={len(ids)}{labels}"]
    for k, fid in enumerate(ids):
        parts.append(f"[f{k}]{FILTERS[fid]['ffmpeg'] or 'null'}[g{k}]")
    parts.append(''.join(f'[g{k}]' for k in range(len(ids))) + f'hstack=inputs={len(ids)}[out]')
    work = Path(tempfile.mkdtemp(prefix='.filtros-', dir=cache))
    try:
        media.run_ffmpeg(['-ss', f'{t:.3f}', '-i', str(folder / p['filename']), '-frames:v', '1',
                          '-filter_complex', ';'.join(parts), '-map', '[out]', '-q:v', '4', str(work / 'tira.jpg')],
                         duration=1.0)
        (work / 'tira.jpg').replace(out)
    except media.FFmpegError as exc:
        raise AppError('Não foi possível gerar as amostras de filtro.', 500, 'filter_sample_failed') from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out
