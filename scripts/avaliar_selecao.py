"""Avalia a seleção automática contra cortes de referência marcados por pessoas.

Uso (somente leitura; não altera projetos):

    python scripts/avaliar_selecao.py <projeto_com_transcricao> <projeto_referencia> [--json saida.json]

- ``projeto_com_transcricao``: pasta em ``data/`` com ``transcript.json`` e ``project.json`` (cenas e duração).
- ``projeto_referencia``: projeto cujos cortes são a referência humana (mesmo vídeo).

Métricas (ver ANALISE_MOTOR_CORTES.md):
- Recall@K: fração dos cortes de referência recuperados entre os K primeiros do ranking.
- Erro de limites: |início - início_ref| e |fim - fim_ref| dos cortes recuperados (s).
- Duplicação: pares de cortes escolhidos com mais de 50% do conteúdo em comum.
- Começo dependente: cortes que começam com conectivo ("e", "mas", "aí"...) ou no meio de uma frase.
- Tempo de seleção (s).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import selection  # noqa: E402


def overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a['end'], b['end']) - max(a['start'], b['start']))


def matches(ref: dict, cand: dict) -> bool:
    """Recuperado: o candidato cobre metade da referência ou metade do candidato está nela."""
    inter = overlap(ref, cand)
    return inter >= 0.5 * (ref['end'] - ref['start']) or inter >= 0.5 * (cand['end'] - cand['start'])


def evaluate(clips: list[dict], refs: list[dict], ks=(5, 10, 18, 30, 50)) -> dict:
    ranked = sorted(clips, key=lambda c: c.get('rank') or 0)
    out = {'cortes': len(ranked)}
    for k in ks + (len(ranked),):
        top = ranked[:k]
        hit = sum(any(matches(r, c) for c in top) for r in refs)
        out[f'recall@{k}' if k != len(ranked) else 'recall@todos'] = round(hit / max(1, len(refs)), 3)
    errs_s, errs_e = [], []
    for r in refs:
        best = max((c for c in ranked if matches(r, c)), key=lambda c: overlap(r, c), default=None)
        if best:
            errs_s.append(abs(best['start'] - r['start']))
            errs_e.append(abs(best['end'] - r['end']))
    med = lambda v: round(sorted(v)[len(v) // 2], 1) if v else None  # noqa: E731
    out['erro_inicio_mediano_s'] = med(errs_s)
    out['erro_fim_mediano_s'] = med(errs_e)
    words = [set(selection._content(selection.tokens(c.get('description') or c.get('title') or ''))) for c in ranked]
    dup = sum(1 for i in range(len(words)) for j in range(i + 1, len(words))
              if selection._similar(words[i], words[j]) > 0.5)
    out['pares_duplicados'] = dup
    dangling = sum(1 for c in ranked
                   if (selection.tokens(c.get('description') or '')[:1] or [''])[0] in selection.DANGLING_START
                   or 'Pode começar no meio de um raciocínio' in (c.get('reasons') or []))
    out['comeco_dependente'] = dangling
    durs = sorted(c['end'] - c['start'] for c in ranked)
    out['duracao_mediana_s'] = round(durs[len(durs) // 2], 1) if durs else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('fonte')
    ap.add_argument('referencia')
    ap.add_argument('--json')
    ap.add_argument('--audio', help='pasta de cache para a energia do áudio; liga a camada de áudio')
    args = ap.parse_args()
    data = ROOT / 'data'
    proj = json.loads((data / args.fonte / 'project.json').read_text(encoding='utf-8'))
    tr = json.loads((data / args.fonte / 'transcript.json').read_text(encoding='utf-8'))
    segments = tr['transcript'] if isinstance(tr, dict) and 'transcript' in tr else tr
    if isinstance(segments, dict):
        segments = segments.get('segments') or []
    refs = json.loads((data / args.referencia / 'project.json').read_text(encoding='utf-8'))['clips']
    target = proj.get('target_seconds') or 35
    audio = None
    if args.audio:
        from app import audio_events
        source = data / args.fonte / proj['filename']
        track = audio_events.energy_track(source, proj['media'], Path(args.audio))
        audio = audio_events.analyze(track, segments)['index']
    t = time.perf_counter()
    clips, info = selection.select_clips(segments, proj['media']['duration'], target, 0, proj.get('scenes') or [],
                                         audio=audio)
    elapsed = time.perf_counter() - t
    from app import config
    report = {'motor': f'select_clips automatico, motor {config.SELECTION_ENGINE}' + (' + audio' if audio else ''),
              'referencias': len(refs),
              'segundos_selecao': round(elapsed, 1), 'candidatos_avaliados': info.get('candidates_evaluated'),
              **evaluate(clips, refs)}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
