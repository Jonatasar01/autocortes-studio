"""Legendas: divisão em blocos legíveis, ASS dimensionado para a saída e SRT auxiliar.

Problema corrigido: o SRT anterior era renderizado com PlayResY padrão (288) do libass,
o que gerava fonte gigante em 9:16 (até 7 linhas cobrindo o vídeo). Agora o ASS usa a
resolução real de saída, com tamanho e margens proporcionais e área segura.
"""
from __future__ import annotations

import re
import unicodedata

MAX_CUE_SECONDS = 4.2
MIN_CUE_SECONDS = 0.6


def clean_text(text: str) -> str:
    """Remove marcação e caracteres que o libass interpretaria como comandos."""
    value = unicodedata.normalize('NFC', str(text or ''))
    value = re.sub(r'<[^>]*>', '', value)
    value = value.replace('{', '(').replace('}', ')').replace('\\', '/')
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def style_for(width: int, height: int) -> dict:
    portrait = height > width * 1.1
    square = not portrait and abs(width - height) <= width * 0.1
    short_side = min(width, height)
    if portrait:
        font = round(width * 0.062)
        margin_v = round(height * 0.17)   # acima da área de botões/legenda do TikTok/Reels/Shorts
    elif square:
        font = round(height * 0.055)
        margin_v = round(height * 0.09)
    else:
        font = round(height * 0.052)
        margin_v = round(height * 0.07)
    margin_h = round(width * 0.08)
    usable = width - 2 * margin_h
    max_chars = max(14, int(usable / (font * 0.56)))
    return {'font': font, 'margin_v': margin_v, 'margin_h': margin_h, 'max_chars': max_chars,
            'outline': max(2, round(short_side * 0.0035)), 'shadow': 1}


def wrap_two_lines(text: str, max_chars: int) -> str:
    """Quebra equilibrada em até duas linhas."""
    if len(text) <= max_chars:
        return text
    words = text.split()
    best, best_diff = text, None
    for i in range(1, len(words)):
        a, b = ' '.join(words[:i]), ' '.join(words[i:])
        if len(a) > max_chars or len(b) > max_chars:
            continue
        diff = abs(len(a) - len(b))
        if best_diff is None or diff < best_diff:
            best, best_diff = a + '\n' + b, diff
    return best


def _chunks_from_words(words: list[dict], limit: int) -> list[tuple[float, float, str]]:
    cues, current = [], []
    for word in words:
        token = clean_text(word.get('word', ''))
        if not token:
            continue
        current.append((float(word['start']), float(word['end']), token))
        text = ' '.join(t for _, _, t in current)
        span = current[-1][1] - current[0][0]
        if len(text) >= limit or span >= MAX_CUE_SECONDS or re.search(r'[.!?…]$', token):
            if len(text) > limit and len(current) > 1:
                last = current.pop()
                cues.append((current[0][0], current[-1][1], ' '.join(t for _, _, t in current)))
                current = [last]
            else:
                cues.append((current[0][0], current[-1][1], text))
                current = []
    if current:
        cues.append((current[0][0], current[-1][1], ' '.join(t for _, _, t in current)))
    return cues


def _chunks_proportional(seg: dict, limit: int) -> list[tuple[float, float, str]]:
    """Sem timestamps de palavras: divide o texto e distribui o tempo pelo nº de caracteres."""
    text = clean_text(seg.get('text', ''))
    if not text:
        return []
    pieces, current = [], ''
    for word in text.split():
        candidate = (current + ' ' + word).strip()
        if len(candidate) > limit and current:
            pieces.append(current)
            current = word
        else:
            current = candidate
        if re.search(r'[.!?…]$', word) and len(current) >= limit * 0.4:
            pieces.append(current)
            current = ''
    if current:
        pieces.append(current)
    start, end = float(seg['start']), float(seg['end'])
    total = sum(len(p) for p in pieces) or 1
    cues, cursor = [], start
    for piece in pieces:
        span = (end - start) * len(piece) / total
        cues.append((cursor, cursor + span, piece))
        cursor += span
    return cues


def build_cues(transcript: list[dict], start: float, end: float, max_chars: int) -> list[dict]:
    """Gera blocos de no máximo duas linhas, relativos ao início do corte."""
    limit = min(max_chars * 2 - 4, 72)  # ~2 linhas, sem blocos longos demais para leitura
    raw: list[tuple[float, float, str]] = []
    for seg in transcript:
        if seg['end'] <= start or seg['start'] >= end:
            continue
        words = [w for w in seg.get('words') or [] if 'start' in w and 'end' in w]
        raw.extend(_chunks_from_words(words, limit) if words else _chunks_proportional(seg, limit))
    cues: list[dict] = []
    for s, e, text in sorted(raw):
        s, e = max(s, start) - start, min(e, end) - start
        if e - s < 0.15 or not text:
            continue
        if e - s < MIN_CUE_SECONDS:
            e = s + MIN_CUE_SECONDS
        cues.append({'start': round(s, 3), 'end': round(e, 3), 'text': wrap_two_lines(text, max_chars)})
    for current, following in zip(cues, cues[1:]):  # sem sobreposição
        if current['end'] > following['start']:
            current['end'] = max(current['start'] + 0.1, following['start'] - 0.02)
    clip_len = end - start
    for cue in cues:
        cue['end'] = min(cue['end'], round(clip_len, 3))
    return [c for c in cues if c['start'] < clip_len and c['end'] > c['start']]


def _ass_time(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'


def srt_timestamp(sec: float) -> str:
    total = int(round(max(0.0, sec) * 1000))
    hours, total = divmod(total, 3600000)
    mins, total = divmod(total, 60000)
    seconds, ms = divmod(total, 1000)
    return f'{hours:02d}:{mins:02d}:{seconds:02d},{ms:03d}'


SUBTITLE_STYLES = ('classica', 'dinamica')
HOOK_SECONDS = 3.0
HIGHLIGHT = '&H0027F804'   # verde (ASS usa &HAABBGGRR)
WORDS_PER_LINE = 3
LINE_GAP = 0.6


def _hook_style(width: int, height: int, font_name: str) -> str:
    size = round(width * 0.052)
    return (f'Style: Hook,{font_name},{size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,'
            f'-1,0,0,0,100,100,0,0,3,{max(8, round(size * 0.25))},0,8,{round(width * 0.07)},'
            f'{round(width * 0.07)},{round(height * 0.11)},1')


HOOK_MAX_CHARS = 90
HOOK_MAX_WORD = 22  # o libass não quebra uma palavra ao meio: pedaços maiores estourariam a largura do quadro


def _hook_text(text: str) -> str:
    """Gancho limpo, com no máximo ``HOOK_MAX_CHARS`` caracteres, cortado em palavra inteira (com "…")."""
    words: list[str] = []
    for word in clean_text(text).split():
        while len(word) > HOOK_MAX_WORD:
            words.append(word[:HOOK_MAX_WORD])
            word = word[HOOK_MAX_WORD:]
        words.append(word)
    out = ' '.join(words)
    if len(out) <= HOOK_MAX_CHARS:
        return out
    return out[:HOOK_MAX_CHARS].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'


def _hook_event(hook_text: str | None, clip_len: float | None) -> list[str]:
    text = _hook_text(hook_text or '')
    if not text:
        return []
    end = HOOK_SECONDS if clip_len is None else min(HOOK_SECONDS, max(0.5, clip_len))
    # O cabeçalho usa WrapStyle 2 (sem quebra automática, bom para as legendas de 3 palavras). O gancho é uma
    # frase longa, então \q0 liga a quebra inteligente dentro das margens do estilo Hook.
    return [f'Dialogue: 1,{_ass_time(0)},{_ass_time(end)},Hook,,0,0,0,,{{\\q0\\fad(150,250)}}{text}']


def to_ass(cues: list[dict], width: int, height: int, font_name: str = 'Arial', *,
           hook_text: str | None = None, clip_len: float | None = None) -> str:
    st = style_for(width, height)
    header = [
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}', f'PlayResY: {height}',
        'WrapStyle: 2', 'ScaledBorderAndShadow: yes', 'YCbCr Matrix: TV.709', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        f"Style: Default,{font_name},{st['font']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{st['outline']},{st['shadow']},2,{st['margin_h']},{st['margin_h']},"
        f"{st['margin_v']},1",
        _hook_style(width, height, font_name),
        '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    lines = _hook_event(hook_text, clip_len)
    lines += [f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Default,,0,0,0,,"
              + c['text'].replace('\n', '\\N') for c in cues]
    return '\n'.join(header + lines) + '\n'


# --------------------------------------------------------------------------- legenda dinâmica

def build_word_lines(transcript: list[dict], start: float, end: float,
                     per_line: int = WORDS_PER_LINE) -> list[list[dict]]:
    """Linhas curtas (até ``per_line`` palavras) com o tempo de cada palavra, relativos ao corte.

    Retorna lista vazia quando a transcrição não tem tempo por palavra.
    """
    words: list[dict] = []
    for seg in transcript:
        if seg['end'] <= start or seg['start'] >= end:
            continue
        for w in seg.get('words') or []:
            text = clean_text(w.get('word', ''))
            if not text or 'start' not in w or 'end' not in w:
                continue
            ws, we = max(float(w['start']), start) - start, min(float(w['end']), end) - start
            if we > ws:
                words.append({'start': round(ws, 3), 'end': round(we, 3), 'text': text})
    words.sort(key=lambda w: w['start'])
    lines: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current and (len(current) >= per_line or w['start'] - current[-1]['end'] > LINE_GAP
                        or re.search(r'[.!?…]$', current[-1]['text'])):
            lines.append(current)
            current = []
        current.append(w)
    if current:
        lines.append(current)
    return lines


def to_ass_dynamic(lines: list[list[dict]], width: int, height: int, font_name: str = 'Arial', *,
                   hook_text: str | None = None, clip_len: float | None = None,
                   highlight: str = HIGHLIGHT) -> str:
    """Legenda em caixa alta, poucas palavras por vez, com a palavra falada destacada."""
    st = style_for(width, height)
    portrait = height > width * 1.1
    size = round(width * (0.078 if portrait else 0.05))
    outline = max(3, round(size * 0.09))
    margin_v = round(height * (0.27 if portrait else 0.12))
    header = [
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}', f'PlayResY: {height}',
        'WrapStyle: 2', 'ScaledBorderAndShadow: yes', 'YCbCr Matrix: TV.709', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        f"Style: Dyn,{font_name},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},2,2,{st['margin_h']},{st['margin_h']},{margin_v},1",
        _hook_style(width, height, font_name),
        '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    events = _hook_event(hook_text, clip_len)
    for line in lines:
        line_end = line[-1]['end']
        if clip_len is not None:
            line_end = min(line_end, clip_len)
        for i, word in enumerate(line):
            a = line[0]['start'] if i == 0 else word['start']
            b = line[i + 1]['start'] if i + 1 < len(line) else line_end
            if b - a < 0.02:
                continue
            parts = []
            for j, other in enumerate(line):
                text = other['text'].upper()
                parts.append(f'{{\\c{highlight}&\\fscx108\\fscy108}}{text}{{\\r}}' if j == i else text)
            pop = '{\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}' if i == 0 else ''
            events.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Dyn,,0,0,0,,{pop}{' '.join(parts)}")
    return '\n'.join(header + events) + '\n'


def hook_only_ass(width: int, height: int, hook_text: str, clip_len: float | None = None,
                  font_name: str = 'Arial') -> str:
    """Só a frase de gancho (quando não há legendas)."""
    return to_ass([], width, height, font_name, hook_text=hook_text, clip_len=clip_len)


def to_srt(cues: list[dict]) -> str:
    blocks = [f"{i}\n{srt_timestamp(c['start'])} --> {srt_timestamp(c['end'])}\n{c['text']}\n"
              for i, c in enumerate(cues, 1)]
    return '\n'.join(blocks)
