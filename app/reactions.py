"""Reações na legenda animada: momentos de pico, som nos picos, vitória/dinheiro em destaque e vícios de fala.

Tudo medido no próprio corte, sem IA:

- **pico** = palavra dita bem mais alto que a mediana do corte (grito, comemoração) ou riso transcrito ("kkkk").
  Fica sozinha na tela, entra grande e treme. Poucos por corte, espaçados, para não virar ruído;
- **som no pico** = efeito sintetizado pelo FFmpeg (nenhum arquivo de áudio de terceiros), misturado na hora;
- **vitória e dinheiro** = palavras como "ganhei", "pix", "mil" recebem o destaque de ênfase;
- **vícios** = hesitações ("éé", "hã") somem da legenda e "tipo tipo" vira "tipo" (o áudio não muda).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import direction

PEAK_DB = 6.0          # pico: palavra 6 dB acima da mediana das palavras do corte…
PEAK_DB_EXCLAIM = 3.0  # …ou 3 dB com exclamação na transcrição
PEAK_GAP = 4.0         # segundos mínimos entre dois picos
PEAK_EVERY = 15.0      # no máximo um pico a cada 15 s de corte…
MAX_PEAKS = 3          # …e nunca mais de três
SOUNDS = ('boom', 'whoosh')
SOUND_LEAD = {'boom': 0.03, 'whoosh': 0.35}   # o whoosh começa antes para "chegar" junto com a palavra
SOUND_VOLUME = 0.55

_LAUGH = re.compile(r'^(k{3,}|(ha){2,}h?|(he){3,}h?|(rs){2,})$')
WIN_WORDS = frozenset('''ganhei ganhou ganhamos ganhar ganhando venci venceu vencemos vitoria campeao campea
campeoes dinheiro grana reais mil milhao milhoes bilhao pix dolar dolares lucro premio premiacao bufunfa gol golaco
acertei acertou clutch ace win money rico rica'''.split())
_HESITATION = re.compile(r'^(e{2,}h*|eh+|ahn|hu+m+|u+h+m*|h+m+)$')   # "é", "há" e gritos ("aaah") ficam
_REPEATABLE = frozenset({'tipo', 'ne', 'assim', 'tipoassim'})


def _fold(text: str) -> str:
    return direction._fold(text)


def clean_fillers(lines: list[list[dict]]) -> list[list[dict]]:
    """Tira hesitações e repetições de muleta da legenda; a palavra seguinte herda o tempo da removida."""
    out = []
    for line in lines:
        kept: list[dict] = []
        for w in line:
            f = _fold(w['text'])
            if _HESITATION.match(f) or (kept and f in _REPEATABLE and _fold(kept[-1]['text']) == f):
                if kept:
                    kept[-1] = {**kept[-1], 'end': w['end']}
                continue
            kept.append(w)
        if kept:
            out.append(kept)
    return out


def find_peaks(words: list[dict], duration: float) -> list[dict]:
    """Palavras de pico (com ``db``), já espaçadas e limitadas. Sem áudio medido, só riso transcrito."""
    levels = [w['db'] for w in words if 'db' in w]
    base = direction._median(levels) if levels else None
    candidates = []
    for w in words:
        f = _fold(w['text'])
        if _LAUGH.match(f):
            candidates.append((99.0, w))
            continue
        if base is None or 'db' not in w or len(f) < 3 or f in direction.STOPWORDS:
            continue
        delta = w['db'] - base
        if delta >= PEAK_DB or (w['text'].endswith('!') and delta >= PEAK_DB_EXCLAIM):
            candidates.append((delta, w))
    budget = min(MAX_PEAKS, max(1, round(duration / PEAK_EVERY)))
    chosen: list[dict] = []
    for _, w in sorted(candidates, key=lambda c: (-c[0], c[1]['start'])):
        if len(chosen) >= budget:
            break
        if all(abs(w['start'] - c['start']) >= PEAK_GAP for c in chosen):
            chosen.append(w)
    return sorted(chosen, key=lambda w: w['start'])


def mark(lines: list[list[dict]], *, peaks: bool, wins: bool, wav_path: Path | None = None,
         duration: float = 0.0) -> int:
    """Marca ``peak`` e ``emph`` nas palavras das linhas (tempos relativos ao corte). Retorna quantos picos."""
    words = [w for line in lines for w in line]
    if wins:
        for w in words:
            if _fold(w['text']) in WIN_WORDS:
                w['emph'] = True
    if not peaks:
        return 0
    direction.word_energy(words, wav_path)
    found = find_peaks(words, duration)
    for w in found:
        w['peak'] = True
    return len(found)


def isolate_peaks(lines: list[list[dict]]) -> list[list[dict]]:
    """A palavra de pico fica sozinha na tela (aparece maior)."""
    out = []
    for line in lines:
        current: list[dict] = []
        for w in line:
            if w.get('peak'):
                if current:
                    out.append(current)
                out.append([w])
                current = []
            else:
                current.append(w)
        if current:
            out.append(current)
    return out


def peak_times(lines: list[list[dict]]) -> list[float]:
    return [w['start'] for line in lines for w in line if w.get('peak')]


def _source(sound: str) -> str:
    if sound == 'boom':
        # Bumbo grave com queda de afinação e decaimento: o "boom" das reações.
        return ("aevalsrc=exprs='0.9*sin(2*PI*(42+110*exp(-16*t))*t)*exp(-3.5*t)':s=48000:d=1.1,"
                'lowpass=f=900')
    # Ruído rosa filtrado que cresce e some rápido: "whoosh".
    return ('anoisesrc=d=0.7:c=pink:r=48000:a=0.6,bandpass=f=1400:width_type=h:w=2200,'
            'afade=t=in:d=0.4:curve=exp,afade=t=out:st=0.4:d=0.3')


def sound_graph(times: list[float], sound: str, main: str, out: str, tail: str = '') -> str:
    """Trecho de ``-filter_complex`` que mistura o efeito em cada pico ao áudio ``main`` e sai em ``out``."""
    parts, labels = [], []
    for k, t in enumerate(times):
        ms = max(0, round((t - SOUND_LEAD[sound]) * 1000))
        parts.append(f'{_source(sound)},volume={SOUND_VOLUME},aformat=sample_rates=48000:channel_layouts=stereo,'
                     f'adelay={ms}:all=1[fx{k}]')
        labels.append(f'[fx{k}]')
    mix = (f"[{main}]aformat=sample_rates=48000:channel_layouts=stereo[mainst];[mainst]{''.join(labels)}"
           f"amix=inputs={len(times) + 1}:duration=first:dropout_transition=0:normalize=0{tail}[{out}]")
    return ';'.join(parts + [mix])
