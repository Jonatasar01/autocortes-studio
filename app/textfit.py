"""Medição tipográfica para legendas ASS: largura real em pixels, sem dependências externas.

Por que existe: agrupar legendas por "número de palavras" ou "número de caracteres" não
garante que o texto caiba no quadro ("PERIGOS DOS ACAMPAMENTOS" tem três palavras e é muito
mais largo que "EU VI ISSO"). Aqui a largura é calculada com as métricas da própria fonte
que o libass vai usar, e o planejador decide as quebras (``\\N``) e, só se preciso, reduz
o tamanho do evento.

- **Fonte:** ``AUTOCORTES_SUBTITLE_FONT`` (caminho de um .ttf), ou a fonte que o libass
  resolve para "Arial" negrito em cada sistema: Arial Bold no Windows, Liberation Sans Bold
  (métricas iguais às do Arial) ou DejaVu Sans Bold (mais larga, portanto conservadora) no
  Linux. Sem nenhum arquivo legível, usa uma estimativa conservadora por caractere.
- **Escala ASS:** no libass/VSFilter o ``Fontsize`` é a altura da linha (ascendente +
  descendente da fonte), não o "em"; a largura em pixels é ``avanços × tamanho ÷ (asc + desc)``.
- **Contorno e sombra:** somam ``2 × outline + shadow`` à largura visível.
"""
from __future__ import annotations

import functools
import os
import struct
from dataclasses import dataclass
from pathlib import Path

FONT_CANDIDATES = (
    Path(__file__).resolve().parent / 'fonts' / 'subtitle-bold.ttf',
    Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'arialbd.ttf',
    Path('/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf'),
    Path('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'),
    Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'),
    Path('/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf'),
    Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
    Path('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf'),
    Path('/Library/Fonts/Arial Bold.ttf'),
    Path('/System/Library/Fonts/Supplemental/Arial Bold.ttf'),
)
# Estimativa sem fonte: largura média de maiúsculas em negrito (≈0,72 da altura de linha) mais folga.
FALLBACK_ADVANCE = 0.74


class FontMetrics:
    """Avanços horizontais de uma fonte TrueType/OpenType (tabelas head, hhea, OS/2, hmtx, cmap)."""

    def __init__(self, path: Path):
        data = path.read_bytes()
        base = 0
        if data[:4] == b'ttcf':  # coleção: usa a primeira fonte
            base = struct.unpack('>I', data[12:16])[0]
        num = struct.unpack('>H', data[base + 4:base + 6])[0]
        tables = {}
        for i in range(num):
            rec = base + 12 + 16 * i
            tag = data[rec:rec + 4].decode('latin-1')
            off, length = struct.unpack('>II', data[rec + 8:rec + 16])
            tables[tag] = (off, length)
        self.path = path
        self._data = data
        head = tables['head'][0]
        self.units_per_em = struct.unpack('>H', data[head + 18:head + 20])[0]
        hhea = tables['hhea'][0]
        asc, desc = struct.unpack('>hh', data[hhea + 4:hhea + 8])
        n_metrics = struct.unpack('>H', data[hhea + 34:hhea + 36])[0]
        line = asc - desc
        if 'OS/2' in tables:
            os2 = tables['OS/2'][0]
            win_asc, win_desc = struct.unpack('>HH', data[os2 + 74:os2 + 78])
            if win_asc + win_desc > 0:
                line = win_asc + win_desc
        self.line_units = line or self.units_per_em
        hmtx = tables['hmtx'][0]
        self.advances = [struct.unpack('>H', data[hmtx + 4 * i:hmtx + 4 * i + 2])[0] for i in range(n_metrics)]
        self._cmap = self._read_cmap(tables['cmap'][0])

    def _read_cmap(self, cmap: int) -> dict[int, int]:
        data = self._data
        n = struct.unpack('>H', data[cmap + 2:cmap + 4])[0]
        subtables = []
        for i in range(n):
            pid, eid, off = struct.unpack('>HHI', data[cmap + 4 + 8 * i:cmap + 12 + 8 * i])
            fmt = struct.unpack('>H', data[cmap + off:cmap + off + 2])[0]
            subtables.append((pid, eid, fmt, cmap + off))
        order = sorted(subtables, key=lambda s: (s[2] != 12, s[2] != 4, s[0] != 3))
        for _pid, _eid, fmt, off in order:
            if fmt == 12:
                return self._cmap12(off)
            if fmt == 4:
                return self._cmap4(off)
        return {}

    def _cmap4(self, off: int) -> dict[int, int]:
        data = self._data
        seg2 = struct.unpack('>H', data[off + 6:off + 8])[0]
        n = seg2 // 2
        ends = struct.unpack(f'>{n}H', data[off + 14:off + 14 + seg2])
        starts_off = off + 16 + seg2
        starts = struct.unpack(f'>{n}H', data[starts_off:starts_off + seg2])
        deltas = struct.unpack(f'>{n}h', data[starts_off + seg2:starts_off + 2 * seg2])
        ro_off = starts_off + 2 * seg2
        ranges = struct.unpack(f'>{n}H', data[ro_off:ro_off + seg2])
        out = {}
        for i in range(n):
            for code in range(starts[i], ends[i] + 1):
                if code == 0xFFFF:
                    continue
                if ranges[i] == 0:
                    glyph = (code + deltas[i]) & 0xFFFF
                else:
                    addr = ro_off + 2 * i + ranges[i] + 2 * (code - starts[i])
                    glyph = struct.unpack('>H', data[addr:addr + 2])[0]
                    if glyph:
                        glyph = (glyph + deltas[i]) & 0xFFFF
                if glyph:
                    out[code] = glyph
        return out

    def _cmap12(self, off: int) -> dict[int, int]:
        data = self._data
        groups = struct.unpack('>I', data[off + 12:off + 16])[0]
        out = {}
        for g in range(groups):
            a, b, glyph = struct.unpack('>III', data[off + 16 + 12 * g:off + 28 + 12 * g])
            if b - a > 0x3000:  # blocos enormes (CJK) não interessam às legendas latinas
                b = a + 0x3000
            for k, code in enumerate(range(a, b + 1)):
                out[code] = glyph + k
        return out

    def advance(self, char: str) -> int:
        glyph = self._cmap.get(ord(char), 0)
        if glyph >= len(self.advances):
            glyph = len(self.advances) - 1
        return self.advances[glyph] if self.advances else self.units_per_em // 2

    def text_width(self, text: str, size: float) -> float:
        """Largura em pixels de ``text`` com ``Fontsize`` ASS = ``size``."""
        return sum(self.advance(c) for c in text) * size / self.line_units


FAMILY_FILES = {
    'arial black': ('ariblk.ttf', 'Arial_Black.ttf', 'Arial Black.ttf'),
    'arial': ('arialbd.ttf', 'Arial_Bold.ttf', 'Arial Bold.ttf'),
    # Fontes que a aba Legendas oferece (arquivo do Windows na versão usada pelo estilo).
    'impact': ('impact.ttf', 'Impact.ttf'),
    'verdana': ('verdanab.ttf', 'Verdana_Bold.ttf', 'Verdana Bold.ttf'),
    'trebuchet ms': ('trebucbd.ttf', 'Trebuchet_MS_Bold.ttf', 'Trebuchet MS Bold.ttf'),
    'segoe ui': ('segoeuib.ttf',),
    'bahnschrift': ('bahnschrift.ttf',),
    'georgia': ('georgiab.ttf', 'Georgia_Bold.ttf', 'Georgia Bold.ttf'),
    'comic sans ms': ('comicbd.ttf', 'Comic_Sans_MS_Bold.ttf', 'Comic Sans MS Bold.ttf'),
}
FONT_DIRS = (Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts',
             Path('/usr/share/fonts/truetype/msttcorefonts'), Path('/Library/Fonts'),
             Path('/System/Library/Fonts/Supplemental'))


def metrics_for_family(family: str) -> FontMetrics | None:
    """Métricas da família usada no estilo ASS; se ela não existir, a mesma substituição do libass."""
    for name in FAMILY_FILES.get(family.strip().lower(), ()):
        for folder in FONT_DIRS:
            if (folder / name).is_file():
                return load_metrics(str(folder / name))
    return load_metrics()


@functools.lru_cache(maxsize=8)
def load_metrics(path: str | None = None) -> FontMetrics | None:
    candidates = [Path(path)] if path else []
    env = os.environ.get('AUTOCORTES_SUBTITLE_FONT')
    if env:
        candidates.append(Path(env))
    candidates += list(FONT_CANDIDATES)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return FontMetrics(candidate)
        except (OSError, struct.error, KeyError, IndexError, ValueError):
            continue
    return None


def text_width(text: str, size: float, metrics: FontMetrics | None = None) -> float:
    metrics = metrics if metrics is not None else load_metrics()
    if metrics is None:
        return len(text) * size * FALLBACK_ADVANCE
    return metrics.text_width(text, size)


# --------------------------------------------------------------------------- planejador

@dataclass
class Layout:
    size: int
    rows: list[list[int]]         # índices das palavras em cada linha
    width: float                  # largura do pior caso (px, com contorno e sombra)
    safe_width: float
    reduced: bool                 # o tamanho foi reduzido para caber

    @property
    def ratio(self) -> float:
        return self.width / self.safe_width if self.safe_width else 0.0


def _row_width(widths: list[float], scales: list[float], row: list[int], space: float,
               active_scale: float) -> float:
    """Largura da linha no pior estado: qualquer palavra da linha pode estar ampliada (destaque)."""
    base = sum(widths[i] * scales[i] for i in row) + space * (len(row) - 1)
    if active_scale <= 1:
        return base
    return max(base + widths[i] * (active_scale - scales[i]) for i in row) if row else base


def _splits(n: int, max_rows: int):
    """Todas as divisões de n palavras em até ``max_rows`` linhas contíguas (n é pequeno)."""
    yield [list(range(n))]
    if max_rows >= 2:
        for a in range(1, n):
            yield [list(range(a)), list(range(a, n))]
    if max_rows >= 3:
        for a in range(1, n - 1):
            for b in range(a + 1, n):
                yield [list(range(a)), list(range(a, b)), list(range(b, n))]


def plan_rows(words: list[str], *, safe_width: float, size: int, min_size: int, outline: float = 0,
              shadow: float = 0, max_rows: int = 2, active_scale: float = 1.0,
              scales: list[float] | None = None, metrics: FontMetrics | None = None) -> Layout:
    """Menor número de linhas (e as mais equilibradas) que cabe em ``safe_width``.

    Reduz o tamanho em passos de 2 px só quando nenhuma divisão cabe. Abaixo de ``min_size``
    calcula o tamanho exato que cabe (palavra única muito longa), nunca deixa estourar.
    """
    metrics = metrics if metrics is not None else load_metrics()
    scales = scales or [1.0] * len(words)
    extra = 2 * outline + shadow
    n = len(words)
    if n == 0:
        return Layout(size, [], 0.0, safe_width, False)
    best: Layout | None = None
    candidate_sizes = list(range(size, min_size - 1, -2)) or [size]
    for s in candidate_sizes:
        widths = [text_width(w, s, metrics) for w in words]
        space = text_width(' ', s, metrics)
        options = []
        for rows in _splits(n, max_rows if n > 1 else 1):
            row_w = [_row_width(widths, scales, r, space, active_scale) + extra for r in rows]
            options.append((len(rows), max(row_w) - min(row_w), max(row_w), rows))
        fitting = [o for o in options if o[2] <= safe_width]
        if fitting:
            count, _imbalance, width, rows = min(fitting, key=lambda o: (o[0], o[1]))
            return Layout(s, rows, width, safe_width, s < size)
        narrowest = min(options, key=lambda o: o[2])
        best = Layout(s, narrowest[3], narrowest[2], safe_width, True)
    # Nem no tamanho mínimo: encolhe proporcionalmente até caber.
    assert best is not None
    factor = (safe_width - extra) / max(1.0, best.width - extra)
    final = max(12, int(best.size * factor))
    widths = [text_width(w, final, metrics) for w in words]
    space = text_width(' ', final, metrics)
    width = max(_row_width(widths, scales, r, space, active_scale) for r in best.rows) + extra
    return Layout(final, best.rows, width, safe_width, True)


def font_source() -> str:
    m = load_metrics()
    return str(m.path) if m else 'estimativa (nenhuma fonte encontrada)'
