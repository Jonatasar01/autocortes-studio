"""Motor de layouts: monta o ``-filter_complex`` que leva o corte para 9:16 (1080×1920).

Todas as cadeias partem de ``[0:v:0]`` e terminam em ``[vout]``. Faixas pretas embutidas no
original (``letterbox``) são cortadas antes de qualquer layout. A legenda ASS, quando existe,
é aplicada no fim da cadeia, já na resolução final.
"""
from __future__ import annotations

from .. import media

OUT_W, OUT_H = 1080, 1920
SRC = '[0:v:0]'


def _fill(w: int, h: int) -> str:
    """Escala cobrindo w×h e recorta o excesso pelo centro (sem barras)."""
    return f'scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,crop={w}:{h},setsar=1'


def single_speaker(subs: str, track: dict | None = None, src: str = SRC) -> str:
    """Recorte vertical centralizado; segue o rosto quando há um plano de ``reframe``."""
    if track and track.get('ok'):
        # Mesmo grafo do editor: recorte que acompanha quem fala e fundo desfocado
        # nas cenas sem rosto visível.
        return media._track_graph(src, '', subs, OUT_W, OUT_H, track)
    return f'{src}{_fill(OUT_W, OUT_H)}{subs}[vout]'


def split_screen(subs: str, src: str = SRC) -> str:
    """Pessoa A (metade esquerda) no topo, pessoa B (metade direita) embaixo."""
    half = OUT_H // 2
    return (f'{src}split=2[ladoA][ladoB];'
            f'[ladoA]crop=iw/2:ih:0:0,{_fill(OUT_W, half)}[topo];'
            f'[ladoB]crop=iw/2:ih:iw/2:0,{_fill(OUT_W, half)}[base];'
            f'[topo][base]vstack=inputs=2,setsar=1{subs}[vout]')


def pip(subs: str, src: str = SRC) -> str:
    """Fundo desfocado preenchendo a tela, com o quadro inteiro sobreposto no centro."""
    return media._blur_branch(src, OUT_W, OUT_H, f',setsar=1{subs}[vout]')


def build(layout: str, subtitles_file: str | None = None, track: dict | None = None,
          letterbox: dict | None = None) -> str:
    # ``subtitles_file`` deve ser um nome simples na pasta de trabalho (cwd do FFmpeg):
    # evita escapar caminhos do Windows (C:\...) dentro do filtro.
    subs = f',ass={subtitles_file}' if subtitles_file else ''
    src = SRC + (f"crop={letterbox['w']}:{letterbox['h']}:{letterbox['x']}:{letterbox['y']}," if letterbox else '')
    if layout == 'single_speaker':
        return single_speaker(subs, track, src)
    if layout == 'split_screen':
        return split_screen(subs, src)
    if layout == 'pip':
        return pip(subs, src)
    raise ValueError(f'layout desconhecido: {layout}')
