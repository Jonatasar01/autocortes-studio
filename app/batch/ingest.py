"""Leitura e validação do arquivo de marcação ``cortes.csv``.

Colunas obrigatórias: ``video_origem``, ``tempo_inicio``, ``tempo_fim``, ``layout``.
Colunas opcionais:

- ``nome``: base do nome do arquivo de saída;
- ``script``: roteiro de narração. Com ele, a linha vira um vídeo narrado (voz sintética como faixa
  principal, áudio original bem baixo ao fundo, legenda gerada da narração). Aceita o texto direto
  (entre aspas, pode ter quebras de linha) ou o caminho de um ``.txt`` (relativo à pasta do CSV);
- ``voz``: voz neural da narração (padrão ``pt-BR-AntonioNeural``).

Cada linha inválida vira um erro com o número da linha, sem interromper as demais.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

LAYOUTS = ('single_speaker', 'split_screen', 'pip')
REQUIRED = ('video_origem', 'tempo_inicio', 'tempo_fim', 'layout')
_LAYOUT_ALIASES = {'single': 'single_speaker', 'split': 'split_screen', 'picture_in_picture': 'pip'}


class CsvError(ValueError):
    """O arquivo não pode ser lido como um todo (cabeçalho ausente, codificação, etc.)."""


@dataclass
class CutRow:
    line: int
    video: Path
    start: float
    end: float
    layout: str
    name: str = ''
    script: str = ''
    voice: str = ''

    @property
    def narrated(self) -> bool:
        return bool(self.script)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class ParseResult:
    rows: list[CutRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_timestamp(text: str) -> float:
    """Converte ``HH:MM:SS``, ``MM:SS`` ou segundos (com fração opcional) em segundos.

    Aceita vírgula como separador decimal (``00:15:30,5``). Levanta ``ValueError`` se inválido.
    """
    value = str(text or '').strip().replace(',', '.')
    parts = value.split(':')
    if not value or len(parts) > 3 or not all(re.fullmatch(r'\d+(\.\d+)?', p) for p in parts):
        raise ValueError(f'tempo inválido: "{text}" (use HH:MM:SS, MM:SS ou segundos)')
    if any('.' in p for p in parts[:-1]):
        raise ValueError(f'tempo inválido: "{text}" (fração só nos segundos)')
    if len(parts) > 1 and any(float(p) >= 60 for p in parts[1:]):
        raise ValueError(f'tempo inválido: "{text}" (minutos e segundos vão de 00 a 59)')
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return round(seconds, 3)


def format_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def _sniff_dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    # Excel em português grava com ";" — aceita os dois separadores.
    try:
        return csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        return csv.excel


def parse_csv(path: Path) -> ParseResult:
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError as exc:
        raise CsvError(f'Arquivo não encontrado: {path}') from exc
    except UnicodeDecodeError:
        text = path.read_text(encoding='cp1252')  # CSV salvo pelo Excel no Windows
    # StringIO (e não splitlines): o roteiro pode ter quebras de linha dentro das aspas.
    reader = csv.DictReader(io.StringIO(text, newline=''), dialect=_sniff_dialect(text[:4096]))
    headers = {h.strip().lower(): h for h in (reader.fieldnames or []) if h}
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        raise CsvError('Colunas ausentes no CSV: ' + ', '.join(missing) +
                       '. Esperado: ' + ', '.join(REQUIRED) + ' (e opcionalmente nome, script, voz).')

    result = ParseResult()
    base = path.resolve().parent
    for raw in reader:
        line = reader.line_num  # número real no arquivo (o DictReader pula linhas vazias)
        row = {k: (raw.get(orig) or '').strip() for k, orig in headers.items()}
        if not any(row.values()):
            continue  # linha em branco
        try:
            result.rows.append(_parse_row(line, row, base))
        except ValueError as exc:
            result.errors.append(f'Linha {line}: {exc}')
    return result


def _parse_row(line: int, row: dict, base: Path) -> CutRow:
    if not row['video_origem']:
        raise ValueError('video_origem vazio')
    video = Path(row['video_origem']).expanduser()
    if not video.is_absolute():
        video = base / video  # caminhos relativos partem da pasta do CSV
    start = parse_timestamp(row['tempo_inicio'])
    end = parse_timestamp(row['tempo_fim'])
    if end <= start:
        raise ValueError(f'tempo_fim ({row["tempo_fim"]}) precisa ser maior que tempo_inicio ({row["tempo_inicio"]})')
    if end - start < 1:
        raise ValueError('o corte precisa durar pelo menos 1 segundo')
    layout = row['layout'].lower().replace('-', '_').replace(' ', '_')
    layout = _LAYOUT_ALIASES.get(layout, layout)
    if layout not in LAYOUTS:
        raise ValueError(f'layout "{row["layout"]}" desconhecido (use {", ".join(LAYOUTS)})')
    return CutRow(line=line, video=video, start=start, end=end, layout=layout, name=row.get('nome', ''),
                  script=_script(row.get('script', ''), base), voice=row.get('voz', ''))


def _script(value: str, base: Path) -> str:
    """Texto do roteiro; um valor terminado em ``.txt`` é lido do arquivo (relativo à pasta do CSV)."""
    if not value.lower().endswith('.txt') or '\n' in value:
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        return path.read_text(encoding='utf-8-sig').strip()
    except FileNotFoundError as exc:
        raise ValueError(f'arquivo de script não encontrado: {path}') from exc
    except UnicodeDecodeError:
        return path.read_text(encoding='cp1252').strip()
