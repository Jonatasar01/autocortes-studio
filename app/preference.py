"""Aprender com o 👍/👎 do usuário: a nota dos trechos se aproxima dos cortes aprovados e se afasta dos rejeitados.

Cada avaliação fica no ``feedback_log`` do projeto com o trecho (início e fim). Aqui o trecho é pontuado de novo pelo
motor atual (as mesmas partes da nota que a seleção calcula: interesse, gancho, assunto, duração…), a partir da
transcrição do projeto, e as partes dos aprovados são comparadas com as dos rejeitados. A diferença de cada parte,
medida em desvios, vira um peso; o ajuste de um trecho novo é a soma ponderada da distância dele ao meio do caminho
entre aprovados e rejeitados, limitado a ±``MAX_ADJUST`` pontos.

Por canal: com avaliações suficientes do mesmo canal, só elas contam (cada streamer tem um estilo); senão, todas.
Tudo local: nada sai do computador.
"""
from __future__ import annotations

import logging
import math

from . import config, selection, storage

log = logging.getLogger('autocortes.preference')

MIN_EACH = 3          # pelo menos 3 aprovados e 3 rejeitados para aprender algo
SHRINK = 10.0         # com poucas avaliações os pesos ficam menores (n / (n + SHRINK))
MIN_WEIGHT = 0.15     # partes que quase não separam aprovados de rejeitados não contam
POINTS = 3.0          # pontos por desvio de distância
MAX_ADJUST = 10.0
SD_FLOOR = 1.0


def channel_of(p: dict) -> str:
    """Canal do projeto: o informado na tela ou, na falta dele, o canal da live na Twitch."""
    name = p.get('channel') or ((p.get('community') or {}).get('video') or {}).get('channel') or ''
    return str(name).strip()


def features(units: list[dict], start: float, end: float, scenes: list[float], rarity) -> dict | None:
    """Partes da nota do trecho, como a seleção as calcula, mais a duração em segundos."""
    idx = [k for k, u in enumerate(units) if u['end'] > start + 0.3 and u['start'] < end - 0.3]
    if not idx:
        return None
    _, breakdown, _ = selection.score_window(units, idx[0], idx[-1], max(1.0, end - start), scenes, rarity)
    out = {k: float(v) for k, v in (breakdown or {}).items()
           if not k.startswith('_') and isinstance(v, (int, float)) and not isinstance(v, bool)}
    out['duracao_s'] = round(end - start, 2)
    return out


def _samples(pid: str) -> list[tuple[str, dict]]:
    p = storage.read_project(pid)
    entries = [e for e in p.get('feedback_log') or [] if e.get('verdict') in ('bom', 'rejeitado')]
    if not entries or not p.get('transcript'):
        return []
    units = selection.build_units(p['transcript'])
    rarity = selection.word_rarity(units) if config.SELECTION_ENGINE != 'v1' else None
    scenes = sorted(p.get('scenes') or [])
    out = []
    for e in entries:
        f = features(units, float(e['start']), float(e['end']), scenes, rarity)
        if f:
            out.append((e['verdict'], f))
    return out


def build(channel: str = '') -> dict | None:
    """Modelo de preferência a partir das avaliações de todos os projetos; ``None`` com avaliações de menos."""
    by_project = []
    for summary in storage.list_projects():
        try:
            p = storage.read_project(summary['id'])
            if not p.get('feedback_log'):
                continue
            by_project.append((channel_of(p).casefold(), _samples(summary['id'])))
        except Exception:   # um projeto ilegível não impede a análise dos outros
            log.warning('Avaliações do projeto %s ignoradas.', summary.get('id'), exc_info=True)
    key = channel.strip().casefold()
    same = [s for ch, samples in by_project if key and ch == key for s in samples]
    scope = 'canal'
    if not _enough(same):
        same, scope = [s for _, samples in by_project for s in samples], 'todos'
    if not _enough(same):
        return None
    good = [f for v, f in same if v == 'bom']
    bad = [f for v, f in same if v == 'rejeitado']
    n = len(same)
    weights = {}
    for name in set.intersection(*(set(f) for f in good + bad)):
        g = [f[name] for f in good]
        b = [f[name] for f in bad]
        mg, mb = sum(g) / len(g), sum(b) / len(b)
        var = (sum((x - mg) ** 2 for x in g) / len(g) + sum((x - mb) ** 2 for x in b) / len(b)) / 2
        sd = max(SD_FLOOR, math.sqrt(var))
        w = (mg - mb) / sd * n / (n + SHRINK)
        if abs(w) >= MIN_WEIGHT:
            weights[name] = (round(w, 3), round((mg + mb) / 2, 3), round(sd, 3))
    if not weights:
        return None
    return {'weights': weights, 'good': len(good), 'bad': len(bad), 'scope': scope, 'channel': channel.strip(),
            'engine': config.SELECTION_ENGINE}


def _enough(samples: list) -> bool:
    return sum(1 for v, _ in samples if v == 'bom') >= MIN_EACH and \
        sum(1 for v, _ in samples if v == 'rejeitado') >= MIN_EACH


def adjust(model: dict | None, breakdown: dict | None, length: float) -> float:
    """Pontos a somar à nota do trecho (negativos quando ele parece com os rejeitados)."""
    if not model or not breakdown:
        return 0.0
    values = {**breakdown, 'duracao_s': length}
    total = 0.0
    for name, (w, mid, sd) in model['weights'].items():
        x = values.get(name)
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            total += w * (x - mid) / sd
    return round(max(-MAX_ADJUST, min(MAX_ADJUST, POINTS * total)), 1)


def summary(model: dict | None) -> dict | None:
    """O que a tela mostra: quantas avaliações e de onde."""
    if not model:
        return None
    return {k: model[k] for k in ('good', 'bad', 'scope', 'channel')}
