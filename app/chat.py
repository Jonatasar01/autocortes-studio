"""Picos no chat da live: rajadas de mensagens e de risadas/emotes apontam os lances que o público sentiu.

O chat vem de um arquivo exportado pelo usuário (a Twitch não oferece o chat das lives gravadas na API oficial):
o JSON do TwitchDownloader (``comments[].content_offset_seconds`` + ``message.body``) ou um texto com uma mensagem
por linha começando pelo tempo desde o início da live (``[0:12:34] nome: mensagem``, também do TwitchDownloader).

As mensagens são contadas em janelas de ``BIN`` segundos e comparadas com a mediana dos ``BASELINE`` segundos ao
redor (cada live tem seu ritmo). Risadas e emotes de reação contam em dobro. Um pico precisa de ``PEAK_RATIO``
vezes o normal e de pelo menos ``MIN_MESSAGES``. O chat reage alguns segundos depois do lance, então o momento
começa ``REACTION_DELAY`` antes do pico.
"""
from __future__ import annotations

import json
import re
import statistics

from .errors import AppError

FILE = 'chat.json'           # na pasta do projeto (fora do project.json: uma live tem centenas de milhares)
MAX_BYTES = 300 * 1024 ** 2
BIN = 5.0
BASELINE = 300.0
PEAK_RATIO = 2.5
MIN_MESSAGES = 8
REACTION_DELAY = 20.0
MIN_GAP = 120.0              # um pico por lance: outro só 2 min depois
PEAKS_PER_HOUR = 8
REACTION_RE = re.compile(r'(?i)\b(?:kekw|lul|lulw|omegalul|kekl|pog|pogchamp|poggers|pogu|monkas|lmao|xd|ez|w)\b|'
                         r'k{4,}|(?:ha){3,}|(?:rs){3,}|^\?+$')
LINE_RE = re.compile(r'^\s*\[?(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?\]?\s+(.*)$')


def parse(raw: bytes) -> list[tuple[float, bool]]:
    """``(segundos desde o início da live, é reação)`` de cada mensagem, em ordem."""
    if len(raw) > MAX_BYTES:
        raise AppError('Arquivo do chat grande demais (máximo de 300 MB).', 413, 'chat_too_big')
    text = raw.decode('utf-8-sig', errors='replace')
    out: list[tuple[float, bool]] = []
    if text.lstrip().startswith('{'):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise AppError('O arquivo do chat não é um JSON válido.', 400, 'chat_bad_file') from None
        for c in data.get('comments') or [] if isinstance(data, dict) else []:
            t = c.get('content_offset_seconds') if isinstance(c, dict) else None
            if isinstance(t, (int, float)) and t >= 0:
                body = ((c.get('message') or {}).get('body') or '') if isinstance(c.get('message'), dict) else ''
                out.append((float(t), bool(REACTION_RE.search(body))))
    else:
        for line in text.splitlines():
            m = LINE_RE.match(line)
            if m:
                h, mi, s, rest = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
                body = rest.split(':', 1)[1] if ':' in rest else rest
                out.append((h * 3600 + mi * 60 + s, bool(REACTION_RE.search(body.strip()))))
    if not out:
        raise AppError('Nenhuma mensagem encontrada. Use o JSON ou o texto do chat exportado pelo TwitchDownloader '
                       '(com o tempo desde o início da live).', 400, 'chat_bad_file')
    out.sort()
    return out


def peaks(messages: list[tuple[float, bool]], duration: float | None = None) -> list[dict]:
    """Picos do chat, do mais forte ao mais fraco: ``start``/``end`` do lance (tempo da live), mensagens e força."""
    if not messages:
        return []
    end_t = max(t for t, _ in messages) if duration is None else duration
    n = int(end_t // BIN) + 1
    counts = [0] * n
    weight = [0.0] * n
    for t, reaction in messages:
        k = int(t // BIN)
        if 0 <= k < n:
            counts[k] += 1
            weight[k] += 2.0 if reaction else 1.0
    half = int(BASELINE / BIN / 2)
    found = []
    for k in range(n):
        if counts[k] < MIN_MESSAGES:
            continue
        around = weight[max(0, k - half):k + half + 1]
        base = max(1.0, statistics.median(around))
        ratio = weight[k] / base
        if ratio >= PEAK_RATIO:
            found.append((ratio, k))
    chosen: list[dict] = []
    limit = max(3, round(PEAKS_PER_HOUR * end_t / 3600))
    for ratio, k in sorted(found, reverse=True):
        t = k * BIN
        if any(abs(t - c['_t']) < MIN_GAP for c in chosen):
            continue
        # O pico continua enquanto as janelas vizinhas seguem bem acima do normal.
        lo, hi = k, k
        while hi + 1 < n and weight[hi + 1] >= weight[k] * 0.5:
            hi += 1
        msgs = sum(counts[lo:hi + 1])
        chosen.append({'_t': t, 'start': max(0.0, t - REACTION_DELAY), 'end': (hi + 1) * BIN, 'messages': msgs,
                       'ratio': round(ratio, 1)})
        if len(chosen) >= limit:
            break
    for c in chosen:
        c.pop('_t')
    return chosen


def compact(messages: list[tuple[float, bool]]) -> dict:
    """Forma guardada no projeto: tempos em décimos de segundo e quais são reações (o texto não é guardado)."""
    return {'times': [round(t * 10) for t, _ in messages], 'reactions': [k for k, (_, r) in enumerate(messages) if r]}


def expand(stored: dict) -> list[tuple[float, bool]]:
    reactions = set(stored.get('reactions') or [])
    return [(t / 10, k in reactions) for k, t in enumerate(stored.get('times') or [])]
