"""Legendas: divisão em blocos legíveis, ASS dimensionado para a saída e SRT auxiliar.

Problema corrigido: o SRT anterior era renderizado com PlayResY padrão (288) do libass,
o que gerava fonte gigante em 9:16 (até 7 linhas cobrindo o vídeo). Agora o ASS usa a
resolução real de saída, com tamanho e margens proporcionais e área segura.
"""
from __future__ import annotations

import re
import unicodedata

from . import textfit

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

# Aparência escolhida na aba Legendas. Fonte: nome da família (libass) -> arquivo para medir a largura e se o
# estilo pede negrito (Impact e Arial Black já são pesadas; negrito nelas seria sintético e mais largo).
SUBTITLE_FONTS = {
    'Arial': ('arialbd.ttf', True), 'Arial Black': ('ariblk.ttf', False), 'Impact': ('impact.ttf', False),
    'Verdana': ('verdanab.ttf', True), 'Trebuchet MS': ('trebucbd.ttf', True), 'Segoe UI': ('segoeuib.ttf', True),
    'Bahnschrift': ('bahnschrift.ttf', True), 'Georgia': ('georgiab.ttf', True),
    'Comic Sans MS': ('comicbd.ttf', True),
}
SIZE_SCALE = {'p': 0.85, 'm': 1.0, 'g': 1.18}
LOOK_DEFAULT = {'font': 'Arial', 'size': 'm', 'color': '#FFFFFF', 'highlight': '#04F827', 'outline': '#000000',
                'background': 'contorno', 'case': 'auto'}
_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')


def clean_look(look: dict | None) -> dict:
    """Aparência válida, com o padrão no que faltar ou vier inválido (o padrão é a legenda de sempre)."""
    look = look or {}
    out = dict(LOOK_DEFAULT)
    if look.get('font') in SUBTITLE_FONTS:
        out['font'] = look['font']
    if look.get('size') in SIZE_SCALE:
        out['size'] = look['size']
    for key in ('color', 'highlight', 'outline'):
        if isinstance(look.get(key), str) and _HEX.match(look[key]):
            out[key] = look[key].upper()
    if look.get('background') in ('contorno', 'caixa', 'nenhum'):
        out['background'] = look['background']
    if look.get('case') in ('auto', 'upper', 'normal'):
        out['case'] = look['case']
    return out


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """``#RRGGBB`` -> ``&HAABBGGRR`` do ASS (alpha 0 = opaco, 255 = transparente)."""
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f'&H{alpha:02X}{b}{g}{r}'.upper()


def _look_style(look: dict, size: int, outline: int, shadow: int, back_alpha: int) -> str:
    """Campos do estilo ASS de ``Fontname`` até ``Shadow`` para a aparência escolhida."""
    bold = -1 if SUBTITLE_FONTS[look['font']][1] else 0
    text = ass_color(look['color'])
    if look['background'] == 'caixa':
        # BorderStyle 3: o contorno vira uma caixa (cor do "contorno", um pouco transparente) com margem.
        pad = max(6, round(size * 0.18))
        return (f"{look['font']},{size},{text},{text},{ass_color(look['outline'], 0x26)},"
                f"{ass_color(look['outline'], 0x26)},{bold},0,0,0,100,100,0,0,3,{pad},0")
    if look['background'] == 'nenhum':   # só o texto: sem contorno e sem sombra
        outline = shadow = 0
    return (f"{look['font']},{size},{text},{text},{ass_color(look['outline'])},&H{back_alpha:02X}000000,"
            f"{bold},0,0,0,100,100,0,0,1,{outline},{shadow}")


def _look_padding(look: dict, size: int, outline: int) -> int:
    if look['background'] == 'nenhum':
        return 0
    return max(6, round(size * 0.18)) if look['background'] == 'caixa' else outline


HOOK_SECONDS = 3.0
HIGHLIGHT = '&H0027F804'   # verde (ASS usa &HAABBGGRR)
EMPHASIS = '&H0000E1FF'    # âmbar: termos enfatizados na fala (plano de edição)
WORDS_PER_LINE = 3
LINE_GAP = 0.6
# Layout medido: o texto ocupa no máximo 95% da largura útil (margens do estilo descontadas).
SAFE_FILL = 0.95
ACTIVE_SCALE = 1.08     # palavra falada no momento (\fscx108)
EMPHASIS_SCALE = 1.12   # termo enfatizado pelo plano de edição (\fscx112)
PEAK_SCALE = 145        # momento de pico (app/reactions.py): sozinho na tela, entra grande e treme
PEAK_OVERSHOOT = 155
PEAK_ANIM = (f'\\fscx60\\fscy60\\t(0,100,\\fscx{PEAK_OVERSHOOT}\\fscy{PEAK_OVERSHOOT})'
             f'\\t(100,180,\\fscx{PEAK_SCALE}\\fscy{PEAK_SCALE})\\t(180,240,\\frz4)\\t(240,300,\\frz-4)'
             '\\t(300,360,\\frz2)\\t(360,420,\\frz0)')


def _new_report() -> dict:
    return {'events': 0, 'max_width_ratio': 0.0, 'reduced_events': 0, 'max_rows': 0, 'min_font': None,
            'font': textfit.font_source()}


def _record(report: dict | None, layout: textfit.Layout, available: float) -> None:
    if report is None:
        return
    report['events'] += 1
    report['max_width_ratio'] = round(max(report['max_width_ratio'], layout.width / available), 3)
    report['reduced_events'] += int(layout.reduced)
    report['max_rows'] = max(report['max_rows'], len(layout.rows))
    report['min_font'] = layout.size if report['min_font'] is None else min(report['min_font'], layout.size)


def _layout_text(text: str, *, available: float, size: int, outline: float, shadow: float, max_rows: int,
                 family: str, report: dict | None = None) -> tuple[str, int]:
    """Texto com quebras ``\\N`` medidas e o tamanho que cabe (igual ao do estilo, quando cabe)."""
    words = text.replace('\n', ' ').split()
    layout = textfit.plan_rows(words, safe_width=available * SAFE_FILL, size=size,
                               min_size=max(12, round(size * 0.72)), outline=outline, shadow=shadow,
                               max_rows=max_rows, metrics=textfit.metrics_for_family(family))
    _record(report, layout, available)
    return '\\N'.join(' '.join(words[i] for i in row) for row in layout.rows), layout.size


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


def _hook_event(hook_text: str | None, clip_len: float | None, width: int, height: int,
                font_name: str = 'Arial', report: dict | None = None) -> list[str]:
    text = _hook_text(hook_text or '')
    if not text:
        return []
    end = HOOK_SECONDS if clip_len is None else min(HOOK_SECONDS, max(0.5, clip_len))
    size = round(width * 0.052)
    pad = max(8, round(size * 0.25))   # caixa opaca (BorderStyle 3): o contorno vira a margem da caixa
    body, fitted = _layout_text(text, available=width - 2 * round(width * 0.07), size=size, outline=pad,
                                shadow=0, max_rows=3, family=font_name, report=report)
    fs = f'\\fs{fitted}' if fitted != size else ''
    # \q2: as quebras já foram medidas; o libass não deve quebrar de novo.
    return [f'Dialogue: 1,{_ass_time(0)},{_ass_time(end)},Hook,,0,0,0,,{{\\q2{fs}\\fad(150,250)}}{body}']


UI_BOTTOM_PORTRAIT = 0.17   # base da tela vertical coberta por botões e legenda do Shorts/Reels/TikTok
UI_BOTTOM_OTHER = 0.07
BAND_GAP = 0.025             # distância entre a borda do vídeo e a legenda


TOP_SAFE = 0.15        # barra do app no topo da tela vertical; a legenda não sobe além
TOP_SAFE_HOOK = 0.25   # com gancho: ele ocupa o topo nos primeiros segundos


def _avoid_face(width: int, height: int, size: int, default_margin: int, avoid: tuple,
                top_safe: float) -> tuple[int, int]:
    """Desvia do rosto. ``avoid`` = ``(topo da cabeça, topo do rosto, queixo)`` em pixels.

    Ordem: a posição de sempre se ela já não encosta na cabeça; abaixo do queixo se couber acima dos botões;
    logo acima da cabeça; sobre o cabelo/fone, acima dos olhos; por fim, a posição de sempre.
    """
    block = round(2 * size * 1.25)          # pior caso: duas linhas
    head, face, chin = avoid
    if height - default_margin <= head or height - default_margin - block >= chin:
        return 2, default_margin
    portrait = height > width * 1.1
    floor = height * (1 - (UI_BOTTOM_PORTRAIT if portrait else UI_BOTTOM_OTHER))
    gap = round(height * BAND_GAP)
    if chin + gap + block <= floor:
        return 8, round(chin + gap)         # abaixo do queixo
    for limit in (head, face):              # acima da cabeça; senão acima dos olhos, sobre o cabelo
        if limit - gap - block >= height * top_safe:
            return 2, round(height - limit + gap)
    return 2, default_margin


def placement(width: int, height: int, size: int, default_margin: int,
              video_band: tuple[int, int] | None = None,
              avoid: tuple | None = None, top_safe: float = TOP_SAFE) -> tuple[int, int]:
    """``(Alignment, MarginV)`` do ASS para a legenda não ficar em cima da borda do vídeo nem do rosto.

    ``video_band`` = faixa vertical ``(topo, base)`` em pixels onde o vídeo aparece (enquadramento com barras
    ou fundo desfocado). A legenda vai inteira para a área logo abaixo do vídeo, acima da zona dos botões;
    se não couber ali, fica inteira dentro do vídeo, apoiada um pouco acima da borda de baixo.
    ``avoid`` = cabeça de quem é seguido no seguir rosto (o vídeo ocupa a tela inteira).
    """
    if not video_band or video_band[1] - video_band[0] >= height * 0.9:
        return _avoid_face(width, height, size, default_margin, avoid, top_safe) if avoid else (2, default_margin)
    top, bottom = video_band
    portrait = height > width * 1.1
    floor = height * (1 - (UI_BOTTOM_PORTRAIT if portrait else UI_BOTTOM_OTHER))
    gap = round(height * BAND_GAP)
    block = round(2 * size * 1.25)          # pior caso: duas linhas
    if bottom + gap + block <= floor:
        return 8, round(bottom + gap)       # ancorada no topo, logo abaixo do vídeo
    return 2, round(height - bottom + gap)  # dentro do vídeo, acima da borda


def to_ass(cues: list[dict], width: int, height: int, font_name: str = 'Arial', *,
           hook_text: str | None = None, clip_len: float | None = None, report: dict | None = None,
           video_band: tuple[int, int] | None = None, look: dict | None = None,
           avoid: tuple[int, int] | None = None) -> str:
    look = clean_look({'font': font_name, **(look or {})})
    font_name = look['font']
    st = style_for(width, height)
    st['font'] = round(st['font'] * SIZE_SCALE[look['size']])
    pad = _look_padding(look, st['font'], st['outline'])
    align, margin_v = placement(width, height, st['font'], st['margin_v'], video_band, avoid,
                                TOP_SAFE_HOOK if hook_text else TOP_SAFE)
    header = [
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}', f'PlayResY: {height}',
        # Quebras decididas pela medição (\\N + \\q2 por evento); WrapStyle 0 é só a rede de segurança.
        'WrapStyle: 0', 'ScaledBorderAndShadow: yes', 'YCbCr Matrix: TV.709', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        f"Style: Default,{_look_style(look, st['font'], st['outline'], st['shadow'], 0x64)},"
        f"{align},{st['margin_h']},{st['margin_h']},{margin_v},1",
        _hook_style(width, height, font_name),
        '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    lines = _hook_event(hook_text, clip_len, width, height, font_name, report)
    available = width - 2 * st['margin_h']
    for c in cues:
        text = c['text'].upper() if look['case'] == 'upper' else c['text']
        body, size = _layout_text(text, available=available, size=st['font'], outline=pad,
                                  shadow=st['shadow'], max_rows=2, family=font_name, report=report)
        fs = f'\\fs{size}' if size != st['font'] else ''
        lines.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Default,,0,0,0,,"
                     f"{{\\q2{fs}}}{body}")
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
                   highlight: str | None = None, report: dict | None = None,
                   video_band: tuple[int, int] | None = None, look: dict | None = None,
                   avoid: tuple[int, int] | None = None) -> str:
    """Legenda em caixa alta, poucas palavras por vez, com a palavra falada destacada.

    O layout de cada grupo é medido uma vez, no pior caso (qualquer palavra ampliada a 108%, termos
    enfatizados a 112%), e fixado com ``\\N``: a linha não muda de quebra quando o destaque anda.
    """
    look = clean_look({'font': font_name, **(look or {})})
    font_name = look['font']
    highlight = highlight or ass_color(look['highlight'])
    st = style_for(width, height)
    portrait = height > width * 1.1
    size = round(width * (0.078 if portrait else 0.05) * SIZE_SCALE[look['size']])
    outline = max(3, round(size * 0.09))
    pad = _look_padding(look, size, outline)
    align, margin_v = placement(width, height, size, round(height * (0.27 if portrait else 0.12)), video_band,
                                avoid, TOP_SAFE_HOOK if hook_text else TOP_SAFE)
    header = [
        '[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {width}', f'PlayResY: {height}',
        'WrapStyle: 0', 'ScaledBorderAndShadow: yes', 'YCbCr Matrix: TV.709', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        f"Style: Dyn,{_look_style(look, size, outline, 2, 0x80)},"
        f"{align},{st['margin_h']},{st['margin_h']},{margin_v},1",
        _hook_style(width, height, font_name),
        '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    events = _hook_event(hook_text, clip_len, width, height, font_name, report)
    available = width - 2 * st['margin_h']
    metrics = textfit.metrics_for_family(font_name)
    for line in lines:
        texts = [w['text'] if look['case'] == 'normal' else w['text'].upper() for w in line]
        peak = len(line) == 1 and line[0].get('peak')
        layout = textfit.plan_rows(texts, safe_width=available * SAFE_FILL, size=size,
                                   min_size=max(12, round(size * 0.72)), outline=pad, shadow=2, max_rows=2,
                                   active_scale=PEAK_OVERSHOOT / 100 if peak else ACTIVE_SCALE,
                                   scales=[EMPHASIS_SCALE if w.get('emph') else 1.0 for w in line],
                                   metrics=metrics)
        _record(report, layout, available)
        fs = f'\\fs{layout.size}' if layout.size != size else ''
        line_end = line[-1]['end']
        if clip_len is not None:
            line_end = min(line_end, clip_len)
        for i, word in enumerate(line):
            a = line[0]['start'] if i == 0 else word['start']
            b = line[i + 1]['start'] if i + 1 < len(line) else line_end
            if b - a < 0.02:
                continue
            if peak:   # sozinho, na cor de destaque, entra grande e treme
                body = '\\N'.join(' '.join(texts[j] for j in row) for row in layout.rows)
                events.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Dyn,,0,0,0,,"
                              f"{{\\q2{fs}\\c{highlight}&{PEAK_ANIM}}}{body}")
                continue
            rows = []
            for row in layout.rows:
                parts = []
                for j in row:
                    other, text = line[j], texts[j]
                    if j == i:
                        parts.append(f'{{\\c{highlight}&\\fscx108\\fscy108}}{text}{{\\r}}')
                    elif other.get('emph'):
                        # Termo enfatizado na fala (plano de edição): âmbar e um pouco maior.
                        parts.append(f'{{\\c{EMPHASIS}&\\fscx112\\fscy112}}{text}{{\\r}}')
                    else:
                        parts.append(text)
                rows.append(' '.join(parts))
            # \r volta ao estilo e apagaria o \fs do evento: reaplica depois de cada palavra destacada.
            body = '\\N'.join(rows).replace('{\\r}', '{\\r' + fs + '}') if fs else '\\N'.join(rows)
            pop = '\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)' if i == 0 else ''
            events.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Dyn,,0,0,0,,{{\\q2{fs}{pop}}}{body}")
    return '\n'.join(header + events) + '\n'


def to_srt(cues: list[dict]) -> str:
    blocks = [f"{i}\n{srt_timestamp(c['start'])} --> {srt_timestamp(c['end'])}\n{c['text']}\n"
              for i, c in enumerate(cues, 1)]
    return '\n'.join(blocks)
