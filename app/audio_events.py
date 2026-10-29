"""Camada de áudio do motor de cortes: energia do vídeo inteiro e eventos sonoros com evidência.

Sem dependências novas: o FFmpeg mede o nível RMS em janelas de 0,2 s (uma passada só no áudio, em cache por
arquivo) e o numpy encontra os eventos. Cada evento traz início, fim, tipo, valor medido, confiança e origem.

Regra do motor: o áudio **reforça** um trecho que já tem conteúdo; sozinho ele não escolhe um corte. Um grito
pode acontecer numa cena qualquer, e o volume de um jogo ou de uma música engana.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import tempfile
import threading
from pathlib import Path

from . import media

log = logging.getLogger('autocortes.audio')

VERSION = 1
HOP = 0.2                    # segundos por medida de energia
RATE = 8000                  # Hz: suficiente para nível de voz, decodifica rápido
FLOOR_DB = -80.0
BASELINE_SECONDS = 60.0      # referência local: mediana do nível em ±30 s
PEAK_DB = 9.0                # pico: 9 dB acima da referência local…
PEAK_MIN = 0.4               # …por pelo menos 0,4 s
VOCAL_DB = 4.0               # som sem palavras transcritas, 4 dB acima da referência: possível risada/reação
VOCAL_MIN = 0.6
SILENCE_DB = -50.0
SILENCE_MIN = 2.5
CLIP_DB = -3.0               # nível RMS colado no máximo: áudio estourado
CLIP_MIN = 1.0
RATE_WINDOW = 4.0
FAST_FACTOR = 1.6
LAUGH_RE = re.compile(r'^(?:k{3,}|(?:ha){2,}h?|(?:rs){2,}|(?:he){3,})$', re.I)


# --------------------------------------------------------------------------- energia

def _stamp(source: Path) -> list:
    st = source.stat()
    return [st.st_size, int(st.st_mtime)]


def energy_track(source: Path, info: dict, cache_dir: Path, *, stamp_of: Path | None = None,
                 cancel_event: threading.Event | None = None) -> dict | None:
    """Nível RMS (dBFS) a cada ``HOP`` segundos do vídeo inteiro, em cache por arquivo e versão.

    ``None`` quando o vídeo não tem áudio. A primeira vez decodifica só a trilha de áudio (cerca de 1–2 min numa
    live de 8 h); depois lê do cache.
    """
    if info.get('audio_index') is None:
        return None
    out = cache_dir / f'audio_energia_v{VERSION}.json'
    # ``stamp_of``: o arquivo original quando ``source`` é o WAV temporário da análise (mesmo áudio, cache do vídeo).
    stamp = _stamp(stamp_of or source)
    if out.is_file():
        try:
            data = json.loads(out.read_text(encoding='utf-8'))
            if data.get('stamp') == stamp and data.get('hop') == HOP:
                data['db'] = _decode(data.pop('levels'))
                return data
        except (ValueError, KeyError):
            pass
    cache_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.energia-', dir=cache_dir))
    try:
        media.run_ffmpeg(['-i', str(source.resolve()), '-map', f"0:{info['audio_index']}", '-vn', '-ac', '1', '-af',
                          f'aresample={RATE},asetnsamples=n={round(HOP * RATE)}:p=0,astats=metadata=1:reset=1,'
                          'ametadata=print:key=lavfi.astats.Overall.RMS_level:file=rms.txt',
                          '-f', 'null', '-'], duration=info['duration'], cwd=work, cancel_event=cancel_event)
        db = [FLOOR_DB if m.group(1) == '-inf' else max(FLOOR_DB, min(0.0, float(m.group(1))))
              for m in re.finditer(r'RMS_level=(-?[\d.]+|-inf)', (work / 'rms.txt').read_text(errors='replace'))]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    data = {'version': VERSION, 'hop': HOP, 'stamp': stamp, 'count': len(db)}
    tmp = cache_dir / f'.audio_energia_v{VERSION}.tmp'
    tmp.write_text(json.dumps({**data, 'levels': _encode(db)}), encoding='utf-8')
    tmp.replace(out)
    return {**data, 'db': db}


def _encode(db: list[float]) -> str:
    """dB em meio dB (0 = −80 dB … 160 = 0 dB): 1 byte por medida."""
    return base64.b64encode(bytes(round((max(FLOOR_DB, min(0.0, v)) - FLOOR_DB) * 2) for v in db)).decode('ascii')


def _decode(text: str) -> list[float]:
    return [b / 2 + FLOOR_DB for b in base64.b64decode(text)]


# --------------------------------------------------------------------------- eventos

def _runs(mask, min_frames: int):
    """Trechos contínuos onde ``mask`` é verdadeiro, com pelo menos ``min_frames`` medidas: ``(início, fim)``."""
    out, start = [], None
    for k, on in enumerate(list(mask) + [False]):
        if on and start is None:
            start = k
        elif not on and start is not None:
            if k - start >= min_frames:
                out.append((start, k))
            start = None
    return out


def _words(transcript: list[dict]) -> list[dict]:
    return [w for seg in transcript or [] for w in seg.get('words') or [] if 'start' in w and 'end' in w]


def analyze(track: dict | None, transcript: list[dict]) -> dict:
    """Eventos sonoros e a base para medir trechos rapidamente (``AudioIndex``)."""
    if not track or not track.get('db'):
        return {'events': [], 'index': None}
    import numpy as np
    hop = track['hop']
    db = np.asarray(track['db'], dtype=np.float32)
    n = len(db)
    # Referência local: mediana móvel (por blocos de 10 s, interpolada), robusta a picos curtos.
    block = max(1, round(10 / hop))
    centers, meds = [], []
    for s in range(0, n, block):
        lo, hi = max(0, s - round(BASELINE_SECONDS / 2 / hop)), min(n, s + round(BASELINE_SECONDS / 2 / hop))
        centers.append(s + block / 2)
        meds.append(float(np.median(db[lo:hi])))
    baseline = np.interp(np.arange(n), centers, meds).astype(np.float32)
    speech = np.zeros(n, dtype=bool)
    words = _words(transcript)
    for w in words:
        speech[max(0, int(w['start'] / hop)):min(n, int(np.ceil(w['end'] / hop)))] = True
    events: list[dict] = []

    def add(kind, a, b, value, confidence):
        events.append({'start': round(a * hop, 2), 'end': round(b * hop, 2), 'tipo': kind, 'valor': round(float(value), 1),
                       'confianca': confidence, 'origem': 'audio'})

    over = db - baseline
    for a, b in _runs(over >= PEAK_DB, round(PEAK_MIN / hop)):
        add('pico_de_volume', a, b, over[a:b].max(), 0.6)
    laugh_frames = np.zeros(n, dtype=bool)
    for w in words:
        if LAUGH_RE.match(re.sub(r'[^\wà-ÿ]', '', str(w.get('word', ''))).lower()):
            laugh_frames[max(0, int(w['start'] / hop)):min(n, int(np.ceil(w['end'] / hop)) + 1)] = True
    # Som forte sem palavras logo depois de fala: riso, grito ou reação (o Whisper não transcreve risada).
    vocal = (over >= VOCAL_DB) & ~speech
    for a, b in _runs(vocal, round(VOCAL_MIN / hop)):
        near_speech = speech[max(0, a - round(1.5 / hop)):a].any() or speech[b:b + round(1.5 / hop)].any()
        if near_speech:
            add('possivel_risada', a, b, over[a:b].mean(), 0.7 if laugh_frames[max(0, a - 5):b + 5].any() else 0.4)
    for a, b in _runs(laugh_frames, 1):
        add('risada_transcrita', a, b, over[a:b].mean() if b > a else 0.0, 0.8)
    for a, b in _runs(db <= SILENCE_DB, round(SILENCE_MIN / hop)):
        add('silencio', a, b, db[a:b].mean(), 0.9)
    for a, b in _runs(db >= CLIP_DB, round(CLIP_MIN / hop)):
        add('audio_estourado', a, b, db[a:b].mean(), 0.8)
    # Ritmo da fala: palavras por segundo em janelas de 4 s comparadas à mediana do vídeo.
    if words:
        win = round(RATE_WINDOW / hop)
        starts = np.zeros(n + 1, dtype=np.int32)
        for w in words:
            k = min(n - 1, max(0, int(w['start'] / hop)))
            starts[k + 1] += 1
        cum = np.cumsum(starts)
        rate = (cum[np.minimum(np.arange(n) + win, n)] - cum[np.arange(n)]) / RATE_WINDOW
        talking = rate[rate > 0]
        if talking.size:
            median_rate = float(np.median(talking))
            for a, b in _runs(rate >= FAST_FACTOR * median_rate, win):
                add('fala_acelerada', a, b, rate[a:b].max() / median_rate, 0.5)
    events.sort(key=lambda e: e['start'])
    return {'events': events, 'index': AudioIndex(db, baseline, events, hop)}


class AudioIndex:
    """Medidas de áudio de qualquer trecho em tempo constante (somas acumuladas), para pontuar milhares de janelas."""

    KINDS = ('pico_de_volume', 'possivel_risada', 'risada_transcrita', 'silencio', 'audio_estourado', 'fala_acelerada')

    def __init__(self, db, baseline, events: list[dict], hop: float):
        import numpy as np
        self.hop = hop
        self.n = len(db)
        # Listas Python, não arrays: o motor consulta centenas de milhares de janelas, um valor por vez, e o acesso
        # escalar do numpy é várias vezes mais lento.
        self._marks = {}
        for kind in self.KINDS:
            mark = np.zeros(self.n, dtype=np.float32)
            for e in events:
                if e['tipo'] == kind:
                    mark[int(e['start'] / hop):max(int(e['start'] / hop) + 1, int(e['end'] / hop))] = 1
            self._marks[kind] = np.concatenate([[0.0], np.cumsum(mark)]).tolist()
        self.events = events

    def _span(self, start: float, end: float) -> tuple[int, int]:
        a = max(0, min(self.n, int(start / self.hop)))
        return a, max(a + 1, min(self.n, int(end / self.hop)))

    def seconds(self, kind: str, start: float, end: float) -> float:
        a, b = self._span(start, end)
        marks = self._marks[kind]
        return (marks[b] - marks[a]) * self.hop

    def events_in(self, start: float, end: float) -> list[dict]:
        return [e for e in self.events if e['start'] < end and e['end'] > start]


# --------------------------------------------------------------------------- reforço no ranking

AUDIO_MAX_BONUS = 10.0
REACTION_BONUS = 5.0
REACTION_AFTER = 2.0         # a risada costuma vir logo depois da última palavra: conta e entra no corte


def reaction_after(index: AudioIndex | None, end: float) -> float | None:
    """Fim de uma reação sonora que começa até ``REACTION_AFTER`` s depois de ``end`` (para estender o corte)."""
    if index is None:
        return None
    ends = [e['end'] for e in index.events_in(end, end + REACTION_AFTER)
            if e['tipo'] in ('possivel_risada', 'risada_transcrita') and e['start'] >= end - 0.5]
    return max(ends) if ends else None


def window_adjustment(index: AudioIndex | None, start: float, end: float, *,
                      has_content: bool) -> tuple[float, list[dict], list[str]]:
    """Ajuste da nota pelo áudio: ``(pontos, evidências, alertas)``.

    Só reforça trechos que já têm conteúdo (``has_content``); alertas técnicos (áudio estourado, silêncio
    longo) valem sempre. Conta a reação no fim do trecho e a que vem logo depois dele (``REACTION_AFTER``).
    """
    if index is None:
        return 0.0, [], []
    dur = max(0.1, end - start)
    evidence, warnings = [], []
    bonus = 0.0
    # Medido na live de referência: reação sonora no fim estava em 28% dos cortes aprovados e em 8% dos cortes do
    # topo não aprovados. Picos e energia média não separavam (numa live de jogo o barulho é do jogo), então não
    # dão pontos.
    if has_content:
        tail = start + 0.6 * dur      # reação perto do fim: o desfecho do que foi contado
        stop = end + REACTION_AFTER
        laugh = index.seconds('possivel_risada', tail, stop) + index.seconds('risada_transcrita', tail, stop)
        if laugh >= 0.6:
            bonus += REACTION_BONUS
            evidence.append({'tipo': 'reação sonora', 'origem': 'audio',
                             'detalhe': f'{laugh:.1f} s de riso ou som sem fala perto do fim'})
    penalty = 0.0
    clipped = index.seconds('audio_estourado', start, end)
    if clipped >= 1.0:
        penalty += 5
        warnings.append(f'Áudio estourado por {clipped:.1f} s')
    silent = index.seconds('silencio', start, end)
    if silent >= 0.25 * dur:
        penalty += 3
        warnings.append(f'{silent:.0f} s de silêncio no trecho')
    return min(AUDIO_MAX_BONUS, bonus) - penalty, evidence, warnings
