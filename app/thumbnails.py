"""Capa automática do corte: um quadro do próprio vídeo exportado com o título grande por cima.

O quadro sai do arquivo exportado (já enquadrado no formato), a 35% do corte, longe do começo que ainda apresenta o
assunto. O texto é o gancho do corte (ou o título), em caixa alta, na fonte Anton com contorno preto, em até três
linhas, no alto da imagem (a legenda gravada no vídeo fica embaixo ou no meio). Fica ao lado da exportação
(``clip_NN_<formato>_capa.jpg``) e sai junto quando a exportação é apagada.
"""
from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path

from . import media, storage, subtitles, textfit
from .errors import AppError

FONT = 'Anton'
AT = 0.35


def _lines(text: str, portrait: bool) -> str:
    text = ' '.join(subtitles.clean_text(text or '').upper().split())
    lines = textwrap.wrap(text, 14 if portrait else 22)[:3]
    if len(textwrap.wrap(text, 14 if portrait else 22)) > 3:
        lines[-1] = lines[-1].rstrip('.,!?') + '…'
    return '\n'.join(lines)


def make(project_id: str, clip_id: int, fmt: str) -> Path:
    """Gera (ou reaproveita) a capa da exportação ``fmt`` do corte e devolve o caminho do JPEG."""
    folder = storage.project_dir(project_id) / 'clips'
    with storage.edit_project(project_id) as p:
        clip = next((c for c in p.get('clips') or [] if c['id'] == clip_id), None)
        record = ((clip or {}).get('exports') or {}).get(fmt)
        if not record or not record.get('file') or not (folder / record['file']).is_file():
            raise AppError('Exporte o corte neste formato antes de gerar a capa.', 409, 'not_exported')
        name = Path(record['file']).stem + '_capa.jpg'
        out = folder / name
        video = folder / record['file']
        if out.is_file() and out.stat().st_mtime >= video.stat().st_mtime:
            record['thumbnail_file'] = name
            return out
        width, height = record.get('width') or 1080, record.get('height') or 1920
        duration = (record.get('validation') or {}).get('duration') or (clip['end'] - clip['start'])
        text = _lines(clip.get('hook_text') or record.get('title') or clip.get('title') or '', height > width * 1.1)
        portrait = height > width * 1.1
        size = round((width * 0.11) if portrait else (height * 0.12))
        # O vídeo exportado já tem a legenda gravada (embaixo ou no meio): o título fica no alto, e só desce
        # quando a legenda foi posta "em cima".
        look_top = record.get('subtitle_position') == 'top'
        top = (0.8 if look_top else 0.2) if portrait else (0.78 if look_top else 0.22)
        work = Path(tempfile.mkdtemp(prefix='.capa-', dir=folder))
        try:
            font_file = subtitles.SUBTITLE_FONTS[FONT][0]
            shutil.copy(textfit.APP_FONTS / font_file, work / font_file)   # caminho relativo: sem escapar "D:\"
            # Uma camada por linha: cada uma centralizada, com espaço fixo entre elas (a Anton tem uma altura de
            # linha grande demais para o espaçamento automático do drawtext).
            lines = text.split('\n') if text else []
            step = round(size * 1.08)
            first = f'h*{top:.2f}-{step * len(lines) / 2:.0f}'
            layers = []
            for k, line in enumerate(lines):
                (work / f'linha{k}.txt').write_bytes(line.encode('utf-8'))   # bytes: sem "\r" do Windows
                layers.append(f"drawtext=fontfile={font_file}:textfile=linha{k}.txt:fontcolor=white:"
                              f"fontsize={size}:borderw={max(4, size // 9)}:bordercolor=black:"
                              f"x=(w-text_w)/2:y={first}+{k * step}")
            draw = ','.join(layers) or 'null'
            media.run_ffmpeg(['-ss', f'{max(0.0, duration * AT):.2f}', '-i', str(video.resolve()), '-frames:v', '1',
                              '-vf', draw, '-q:v', '3', str((work / 'capa.jpg').resolve())],
                             duration=1.0, cwd=work, timeout=120)
            (work / 'capa.jpg').replace(out)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        record['thumbnail_file'] = name
        return out
