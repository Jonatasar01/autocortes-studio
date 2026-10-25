"""Legendas de retenção: transcrição do trecho (faster-whisper) e ASS em blocos curtos.

Estilo: fonte grossa, amarela, contorno preto, centralizada na tela, em blocos de
3 a 5 palavras. Os tempos vêm das palavras reconhecidas pelo Whisper; sem tempo por
palavra, o texto do segmento é dividido proporcionalmente.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from .. import media, transcription
from .. import textfit
from ..subtitles import _ass_time, clean_text

MIN_WORDS, MAX_WORDS = 3, 5
MAX_CHARS = 26          # evita blocos de 4–5 palavras longas que não cabem em 1080 px
GAP_BREAK = 0.6         # pausa (s) que encerra o bloco atual
HOLD = 0.15             # o bloco fica na tela um pouco depois da última palavra
MIN_EVENT = 0.3

YELLOW = '&H0000FFFF'   # ASS usa &HAABBGGRR
BLACK = '&H00000000'
SHADOW = '&H96000000'


def extract_audio(clip: Path, offset: float, duration: float, dest: Path, *,
                  cancel_event: threading.Event | None = None) -> Path:
    """WAV 16 kHz mono só do trecho pedido (a margem do quadro-chave fica de fora)."""
    media.run_ffmpeg(['-ss', f'{offset:.6f}', '-i', str(clip), '-t', f'{duration:.3f}', '-map', '0:a:0',
                      '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(dest)],
                     duration=duration, cancel_event=cancel_event)
    return dest


def transcribe(wav: Path, duration: float, *, cancel_event: threading.Event | None = None) -> tuple[list[dict], dict]:
    # A escolha GPU/CPU (e o fallback depois de uma falha do CUDA) fica em app.transcription.
    return transcription.transcribe(wav, duration, cancel_event=cancel_event)


def _words(transcript: list[dict], duration: float) -> list[dict]:
    words: list[dict] = []
    for seg in transcript:
        seg_words = [w for w in seg.get('words') or [] if clean_text(w.get('word', ''))]
        if seg_words:
            for w in seg_words:
                words.append({'start': float(w['start']), 'end': float(w['end']), 'text': clean_text(w['word'])})
            continue
        # Sem tempo por palavra: distribui o segmento proporcionalmente ao tamanho das palavras.
        tokens = clean_text(seg.get('text', '')).split()
        if not tokens:
            continue
        total = sum(len(t) for t in tokens)
        t, span = float(seg['start']), max(0.1, float(seg['end']) - float(seg['start']))
        for token in tokens:
            step = span * len(token) / total
            words.append({'start': t, 'end': t + step, 'text': token})
            t += step
    words = [w for w in words if w['start'] < duration]
    for w in words:
        w['start'], w['end'] = max(0.0, w['start']), min(duration, max(w['end'], w['start'] + 0.05))
    return sorted(words, key=lambda w: w['start'])


def join_words(tokens) -> str:
    """Junta palavras; pedaços como "-vindos" ou "," colam na anterior (Whisper divide assim)."""
    out = ''
    for token in tokens:
        glue = not out or re.match(r'^[-,.!?…:;)\]]', token)
        out += token if glue else ' ' + token
    return out


def group_words(words: list[dict], max_words: int = 4) -> list[list[dict]]:
    """Blocos de até ``max_words`` (3–5) palavras, quebrando em pausas e fim de frase."""
    max_words = min(MAX_WORDS, max(MIN_WORDS, int(max_words)))
    blocks: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current:
            text_len = sum(len(x['text']) + 1 for x in current) + len(w['text'])
            if (len(current) >= max_words or text_len > MAX_CHARS
                    or w['start'] - current[-1]['end'] > GAP_BREAK
                    or re.search(r'[.!?…]$', current[-1]['text'])):
                blocks.append(current)
                current = []
        current.append(w)
    if current:
        blocks.append(current)
    return blocks


def to_ass(blocks: list[list[dict]], *, width: int = 1080, height: int = 1920, font: str = 'Arial Black',
           uppercase: bool = True, clip_len: float | None = None) -> str:
    size = round(width * 0.082)
    outline = max(4, round(size * 0.085))
    header = [
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}', f'PlayResY: {height}',
        'WrapStyle: 0', 'ScaledBorderAndShadow: yes', 'YCbCr Matrix: TV.709', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        # Alignment 5 = centro da tela (horizontal e vertical).
        f'Style: Retencao,{font},{size},{YELLOW},{YELLOW},{BLACK},{SHADOW},'
        f'-1,0,0,0,100,100,0,0,1,{outline},3,5,{round(width * 0.07)},{round(width * 0.07)},0,1',
        '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    events = []
    metrics = textfit.metrics_for_family(font)
    for i, block in enumerate(blocks):
        start = block[0]['start']
        end = block[-1]['end'] + HOLD
        if i + 1 < len(blocks):
            end = min(end, blocks[i + 1][0]['start'])
        if clip_len is not None:
            end = min(end, clip_len)
        if end - start < MIN_EVENT:
            end = start + MIN_EVENT
        text = join_words(w['text'] for w in block)
        text = text.upper() if uppercase else text
        # Quebras medidas na fonte real (no máximo 2 linhas); o tamanho só cai se nada couber.
        tokens = text.split()
        layout = textfit.plan_rows(tokens, safe_width=(width - 2 * round(width * 0.07)) * 0.95, size=size,
                                   min_size=round(size * 0.72), outline=outline, shadow=3, max_rows=2,
                                   metrics=metrics)
        body = '\\N'.join(' '.join(tokens[k] for k in row) for row in layout.rows)
        fs = f'\\fs{layout.size}' if layout.size != size else ''
        # Leve "pop" de entrada: 90% → 100% em 80 ms.
        events.append(f'Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Retencao,,0,0,0,,'
                      f'{{\\q2{fs}\\fscx90\\fscy90\\t(0,80,\\fscx100\\fscy100)}}{body}')
    return '\n'.join(header + events) + '\n'


def build_ass(transcript: list[dict], duration: float, *, max_words: int = 4, font: str = 'Arial Black',
              uppercase: bool = True) -> str | None:
    blocks = group_words(_words(transcript, duration), max_words)
    if not blocks:
        return None
    return to_ass(blocks, font=font, uppercase=uppercase, clip_len=duration)
