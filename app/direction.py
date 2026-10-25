"""Motor de direção criativa: analisa um corte e gera um plano de edição verificável.

Nada aqui é aleatório. Cada ação do plano vem de uma evidência medida no próprio trecho:

- **tempo de cada palavra** (faster-whisper) → ritmo da fala e pausas;
- **energia do áudio por palavra** (RMS em janelas de 20 ms) → ênfases reais na voz;
- **pontuação** da transcrição (``?``, ``!``) → perguntas, exclamações, fim de frase;
- **mudanças de cena** já detectadas na análise do projeto → zoom não atravessa um corte.

O plano é um dicionário serializável (salvo no projeto) com ``actions``: tipo, início e fim
(segundos relativos ao corte), parâmetros, justificativa e ``enabled``. O usuário pode
desligar ações antes de exportar; a exportação grava o plano usado, o que torna o resultado
reproduzível.

Sem transcrição, a análise informa a limitação e o plano fica restrito ao que não depende
da fala (enquadramento, cor, áudio), em vez de inventar ênfases.
"""
from __future__ import annotations

import array
import math
import re
import threading
import unicodedata
import wave
from pathlib import Path

from . import media, reframe

STYLES = {
    'impacto': 'Impacto dinâmico',
    'equilibrado': 'Equilibrado',
    'cinematografico': 'Cinematográfico',
    'podcast': 'Podcast premium',
}
# Versões automáticas: diferenças reais de ritmo, zoom, pausas e cor.
VARIANTS = ('impacto', 'equilibrado', 'cinematografico')
ACTION_LABELS = {'zoom': 'Zoom', 'push': 'Aproximação lenta', 'cut_pause': 'Encurtar pausa',
                 'highlight': 'Destaque na legenda'}

# Parâmetros de cada estilo na intensidade máxima (1.0); a intensidade escala zoom e cortes.
PRESETS: dict[str, dict] = {
    'impacto': {'zoom': 0.14, 'zoom_per_10s': 1.2, 'zoom_gap': 2.5, 'pause_over': 0.45, 'pause_keep': 0.12,
                'subtitle_style': 'dinamica', 'highlights': 6, 'framing': 'track',
                'color': {'contrast': 1.05, 'saturation': 1.10}, 'video_fade': 0.0, 'push': 0.0},
    'equilibrado': {'zoom': 0.08, 'zoom_per_10s': 0.6, 'zoom_gap': 4.0, 'pause_over': 0.75, 'pause_keep': 0.2,
                    'subtitle_style': 'dinamica', 'highlights': 4, 'framing': 'blur',
                    'color': None, 'video_fade': 0.0, 'push': 0.0},
    'cinematografico': {'zoom': 0.0, 'zoom_per_10s': 0.0, 'zoom_gap': 99.0, 'pause_over': 1.4, 'pause_keep': 0.5,
                        'subtitle_style': 'classica', 'highlights': 0, 'framing': 'blur',
                        'color': {'contrast': 1.07, 'saturation': 0.90, 'vignette': True}, 'video_fade': 0.4,
                        'push': 0.05},
    'podcast': {'zoom': 0.06, 'zoom_per_10s': 0.4, 'zoom_gap': 5.0, 'pause_over': 0.8, 'pause_keep': 0.25,
                'subtitle_style': 'dinamica', 'highlights': 3, 'framing': 'track',
                'color': None, 'video_fade': 0.0, 'push': 0.0},
}
DEFAULT_OPTIONS = {'zoom': True, 'dynamic_subtitles': True, 'audio': True, 'denoise': False,
                   'remove_pauses': True, 'reframe': True}
ZOOM_MAX = 0.2           # limite absoluto de aproximação (20%): nunca corta informação demais
ZOOM_IN, ZOOM_OUT = 0.25, 0.35   # rampas suaves (s)
MIN_ZOOM_SECONDS = 0.8
STOPWORDS = set('''a o e é de da do das dos em no na nos nas um uma uns umas que se por para pra com sem
ao aos à às ou mas mais menos muito muita eu tu ele ela nós vós eles elas me te lhe nos vos meu minha seu
sua isso isto aquilo esse essa este esta lá aqui aí já não sim também então só como quando onde
the and of to in is it you that'''.split())
_LAUGH = re.compile(r'^(k{3,}|(ha){2,}h?|(he){2,}h?|(rs){2,}|risos?)$')
_EXPLAIN = {'porque', 'entao', 'primeiro', 'segundo', 'exemplo', 'significa', 'processo', 'passo', 'motivo',
            'resumo', 'basicamente', 'funciona', 'importante'}
_NARRATIVE = {'quando', 'depois', 'dia', 'era', 'foi', 'tinha', 'estava', 'aconteceu', 'lembro', 'contei'}


def _fold(word: str) -> str:
    value = unicodedata.normalize('NFKD', word.lower()).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', value)


# --------------------------------------------------------------------------- análise

def clip_words(transcript: list[dict], start: float, end: float) -> list[dict]:
    """Palavras do corte com tempos relativos ao início do corte."""
    out = []
    for seg in transcript or []:
        if seg['end'] <= start or seg['start'] >= end:
            continue
        seg_text = seg.get('text', '').strip()
        words = [w for w in seg.get('words') or [] if 'start' in w and 'end' in w and w.get('word', '').strip()]
        for i, w in enumerate(words):
            a, b = float(w['start']) - start, float(w['end']) - start
            if b <= 0 or a >= end - start:
                continue
            text = w['word'].strip()
            out.append({'start': round(max(0.0, a), 3), 'end': round(min(end - start, b), 3), 'text': text,
                        'last_in_segment': i == len(words) - 1, 'segment_end_char': seg_text[-1:]})
    return sorted(out, key=lambda w: w['start'])


def _rms_frames(wav_path: Path, frame_ms: int = 20) -> tuple[list[float], float]:
    with wave.open(str(wav_path), 'rb') as wf:
        if wf.getsampwidth() != 2:
            return [], frame_ms / 1000
        rate, channels = wf.getframerate(), wf.getnchannels()
        data = array.array('h', wf.readframes(wf.getnframes()))
    if channels > 1:
        data = data[::channels]
    step = max(1, int(rate * frame_ms / 1000))
    frames = []
    for i in range(0, len(data) - step + 1, step):
        chunk = data[i:i + step]
        frames.append(math.sqrt(sum(x * x for x in chunk) / step) / 32768.0)
    return frames, frame_ms / 1000


def word_energy(words: list[dict], wav_path: Path | None) -> None:
    """Acrescenta ``db`` (nível RMS da palavra, dBFS) a cada palavra, se houver áudio."""
    if wav_path is None or not wav_path.is_file():
        return
    frames, hop = _rms_frames(wav_path)
    if not frames:
        return
    for w in words:
        a, b = int(w['start'] / hop), max(int(w['start'] / hop) + 1, int(w['end'] / hop))
        chunk = frames[a:b] or frames[max(0, a - 1):a + 1]
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk)) if chunk else 0.0
        w['db'] = round(20 * math.log10(max(rms, 1e-6)), 1)


def extract_clip_audio(source: Path, info: dict, start: float, end: float, dest: Path, *,
                       cancel_event: threading.Event | None = None) -> Path | None:
    if info.get('audio_index') is None:
        return None
    media.run_ffmpeg(['-ss', f'{start:.3f}', '-i', str(source), '-t', f'{end - start:.3f}',
                      '-map', f"0:{info['audio_index']}", '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                      str(dest)], duration=end - start, cancel_event=cancel_event)
    return dest


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2) if n else 0.0


def analyze(transcript: list[dict], start: float, end: float, *, scenes: list[float] | None = None,
            wav_path: Path | None = None) -> dict:
    """Características medidas do trecho. Tempos relativos ao corte."""
    duration = round(end - start, 3)
    words = clip_words(transcript, start, end)
    word_energy(words, wav_path)
    cuts = sorted(round(s - start, 3) for s in (scenes or []) if start + 0.3 < s < end - 0.3)
    result: dict = {'duration': duration, 'scene_cuts': cuts, 'words': words, 'limitations': []}
    if not words:
        result.update(content_type='desconhecido', confidence='nenhuma', evidence=[], speech_rate=None,
                      pauses=[], emphasis=[])
        result['limitations'].append('Sem transcrição com tempo por palavra: ritmo, pausas e ênfases não foram '
                                     'avaliados. O plano usa só enquadramento, cor e áudio.')
        return result

    speech_time = sum(w['end'] - w['start'] for w in words)
    rate = round(len(words) / max(1.0, duration), 2)
    folded = [_fold(w['text']) for w in words]
    questions = sum(1 for w in words if w['last_in_segment'] and w['segment_end_char'] == '?')
    exclaims = sum(1 for w in words if w['last_in_segment'] and w['segment_end_char'] == '!')
    laughs = sum(1 for f in folded if _LAUGH.match(f))
    explain = sum(1 for f in folded if f in _EXPLAIN)
    narrative = sum(1 for f in folded if f in _NARRATIVE)

    pauses = []
    for prev, nxt in zip(words, words[1:]):
        gap = nxt['start'] - prev['end']
        if gap >= 0.3:
            ends_sentence = bool(re.search(r'[.!?…]$', prev['text'])) or (prev['last_in_segment'] and
                                                                         prev['segment_end_char'] in '.!?…')
            pauses.append({'start': prev['end'], 'end': nxt['start'], 'gap': round(gap, 3),
                           'after_question': prev['text'].endswith('?') or
                           (prev['last_in_segment'] and prev['segment_end_char'] == '?'),
                           'sentence_end': ends_sentence, 'before': nxt['text'], 'after': prev['text']})

    emphasis = []
    levels = [w['db'] for w in words if 'db' in w]
    if levels:
        base = _median(levels)
        for w, f in zip(words, folded):
            if 'db' not in w or len(f) < 3 or f in STOPWORDS:
                continue
            delta = round(w['db'] - base, 1)
            exclaim = w['last_in_segment'] and w['segment_end_char'] == '!'
            if delta >= 4.0 or (exclaim and delta >= 1.5):
                emphasis.append({'start': w['start'], 'end': w['end'], 'text': w['text'], 'delta_db': delta,
                                 'exclamation': exclaim})
    else:
        result['limitations'].append('Sem áudio legível: ênfases da voz não foram medidas.')

    evidence = [f'{rate:.1f} palavras/s', f'{questions} pergunta(s)', f'{exclaims} exclamação(ões)',
                f'{len(pauses)} pausa(s) ≥ 0,3 s']
    if laughs:
        content, conf = 'humor', 'média'
        evidence.append(f'{laughs} marca(s) de riso na transcrição')
    elif questions >= 2:
        content, conf = 'entrevista', 'média' if questions >= 3 else 'baixa'
    elif explain >= 2 and explain >= narrative:
        content, conf = 'explicação', 'baixa'
        evidence.append(f'{explain} conectivo(s) explicativo(s)')
    elif narrative >= 2:
        content, conf = 'narrativa', 'baixa'
        evidence.append(f'{narrative} marca(s) narrativa(s)')
    else:
        content, conf = 'outro', 'baixa'
    result.update(content_type=content, confidence=conf, evidence=evidence, speech_rate=rate,
                  speech_ratio=round(speech_time / max(0.1, duration), 2), questions=questions,
                  exclamations=exclaims, pauses=pauses, emphasis=emphasis)
    return result


def choose_style(analysis: dict) -> tuple[str, str]:
    """Estilo para o modo "Inteligente", com a justificativa."""
    ctype, rate = analysis.get('content_type'), analysis.get('speech_rate')
    if rate is None:
        return 'equilibrado', 'Sem transcrição para avaliar o conteúdo: estilo equilibrado, sem efeitos ligados à fala.'
    if ctype == 'humor':
        return 'impacto', 'Riso na transcrição: ritmo rápido e zoom nas reações combinam com humor.'
    if ctype == 'entrevista':
        return 'podcast', 'Várias perguntas no trecho: conversa, então foco em quem fala e movimentos suaves.'
    if ctype == 'narrativa' or rate < 2.0:
        return 'cinematografico', (f'Fala pausada ({rate:.1f} palavras/s) ou narrativa: pausas e ritmo '
                                   'preservados, transições discretas.')
    if rate >= 3.2 and len(analysis.get('emphasis') or []) >= 2:
        return 'impacto', f'Fala rápida ({rate:.1f} palavras/s) com ênfases marcadas na voz.'
    return 'equilibrado', 'Sem sinal forte de um gênero específico: edição equilibrada.'


# --------------------------------------------------------------------------- plano

def _inside_scene(a: float, b: float, cuts: list[float]) -> tuple[float, float]:
    """Limita o intervalo para não atravessar uma mudança de cena."""
    for c in cuts:
        if a < c < b:
            if c - a >= b - c:
                b = c - 0.05
            else:
                a = c + 0.05
    return a, b


def build_plan(analysis: dict, style: str = 'auto', intensity: float = 0.6, options: dict | None = None) -> dict:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    intensity = min(1.0, max(0.0, float(intensity)))
    reason = ''
    if style == 'auto':
        style, reason = choose_style(analysis)
    if style not in PRESETS:
        raise ValueError(f'estilo desconhecido: {style}')
    preset = PRESETS[style]
    duration = analysis['duration']
    cuts = analysis.get('scene_cuts') or []
    actions: list[dict] = []

    # Zoom em ênfases reais da voz, espaçado e limitado por intensidade.
    zoom_amount = min(ZOOM_MAX, preset['zoom'] * intensity)
    if opts['zoom'] and zoom_amount >= 0.02 and analysis.get('emphasis'):
        budget = max(1, round(duration / 10 * preset['zoom_per_10s'] * max(0.3, intensity)))
        ranked = sorted(analysis['emphasis'], key=lambda e: (-e['delta_db'], e['start']))
        chosen: list[dict] = []
        for e in ranked:
            if len(chosen) >= budget:
                break
            if any(abs(e['start'] - c['start']) < preset['zoom_gap'] for c in chosen):
                continue
            a, b = _inside_scene(max(0.0, e['start'] - 0.2), min(duration, e['end'] + 0.9), cuts)
            if b - a < MIN_ZOOM_SECONDS:
                continue
            chosen.append({**e, 'a': a, 'b': b})
        for e in sorted(chosen, key=lambda x: x['a']):
            why = f"ênfase na voz em “{e['text']}” ({e['delta_db']:+.1f} dB sobre a mediana do trecho)"
            if e['exclamation']:
                why += ', fim de exclamação'
            actions.append({'type': 'zoom', 'start': round(e['a'], 3), 'end': round(e['b'], 3),
                            'params': {'amount': round(zoom_amount, 3)}, 'reason': why[0].upper() + why[1:]})

    # Aproximação lenta: só em plano longo sem corte de cena (evita imagem estática).
    if opts['zoom'] and preset['push'] > 0 and intensity > 0:
        spans = list(zip([0.0] + cuts, cuts + [duration]))
        a, b = max(spans, key=lambda s: s[1] - s[0])
        if b - a >= 6:
            actions.append({'type': 'push', 'start': round(a, 3), 'end': round(b, 3),
                            'params': {'amount': round(min(ZOOM_MAX, preset['push'] * intensity), 3)},
                            'reason': f'Plano de {b - a:.0f} s sem mudança de cena: aproximação lenta para não '
                                      'ficar estático.'})

    # Pausas: encurta as longas, preserva as que têm função narrativa.
    if opts['remove_pauses']:
        threshold = preset['pause_over'] / max(0.5, 0.5 + intensity)
        for p in analysis.get('pauses') or []:
            if p['gap'] < threshold:
                continue
            dramatic = p['after_question'] or (style in ('cinematografico', 'podcast') and p['sentence_end']
                                                and p['gap'] < threshold * 1.6)
            if dramatic:
                continue
            keep = preset['pause_keep']
            cut_a, cut_b = round(p['start'] + keep / 2, 3), round(p['end'] - keep / 2, 3)
            if cut_b - cut_a < 0.15:
                continue
            actions.append({'type': 'cut_pause', 'start': cut_a, 'end': cut_b, 'params': {'gap': p['gap']},
                            'reason': f"Pausa de {p['gap']:.2f} s entre “{p['after']}” e “{p['before']}”; "
                                      f"mantidos {keep:.2f} s para a fala respirar."})

    # Destaques na legenda: as ênfases mais fortes (não inventa palavras, só realça as faladas).
    if opts['dynamic_subtitles'] and preset['highlights'] and analysis.get('emphasis'):
        top = sorted(analysis['emphasis'], key=lambda e: -e['delta_db'])[:max(1, round(preset['highlights']
                                                                                     * max(0.3, intensity)))]
        for e in sorted(top, key=lambda x: x['start']):
            actions.append({'type': 'highlight', 'start': e['start'], 'end': e['end'], 'params': {'word': e['text']},
                            'reason': f"“{e['text']}” é dita com mais intensidade ({e['delta_db']:+.1f} dB)."})

    actions.sort(key=lambda x: (x['start'], x['type']))
    for i, act in enumerate(actions, 1):
        act.update(id=i, enabled=True, label=ACTION_LABELS[act['type']])

    framing = preset['framing'] if opts['reframe'] else 'blur'
    if framing == 'track' and not reframe.available():
        framing = 'blur'
    settings = {
        'framing': framing,
        'subtitle_style': preset['subtitle_style'] if opts['dynamic_subtitles'] else 'classica',
        'normalize_audio': bool(opts['audio']),
        'denoise': bool(opts['audio'] and opts['denoise']),
        'audio_fade': 0.05 if opts['audio'] else 0.0,
        'video_fade': preset['video_fade'],
        'color': preset['color'] if intensity > 0 else None,
    }
    return {'style': style, 'style_label': STYLES[style], 'intensity': round(intensity, 2), 'options': opts,
            'reason': reason or f'Estilo escolhido: {STYLES[style]}.', 'settings': settings, 'actions': actions,
            'analysis': summary(analysis), 'version': 1}


def summary(analysis: dict) -> dict:
    """Resumo gravado no plano (sem a lista completa de palavras)."""
    keys = ('duration', 'content_type', 'confidence', 'evidence', 'speech_rate', 'limitations', 'scene_cuts')
    out = {k: analysis.get(k) for k in keys}
    out['emphasis_count'] = len(analysis.get('emphasis') or [])
    out['pause_count'] = len(analysis.get('pauses') or [])
    return out


def describe(plan: dict) -> dict:
    """Números para comparar versões: o que cada uma realmente muda."""
    active = [a for a in plan['actions'] if a.get('enabled', True)]
    removed = sum(a['end'] - a['start'] for a in active if a['type'] == 'cut_pause')
    duration = plan['analysis']['duration']
    return {'zooms': sum(1 for a in active if a['type'] in ('zoom', 'push')),
            'pauses_cut': sum(1 for a in active if a['type'] == 'cut_pause'),
            'seconds_removed': round(removed, 2), 'final_duration': round(duration - removed, 2),
            'highlights': sum(1 for a in active if a['type'] == 'highlight'),
            'framing': plan['settings']['framing'], 'subtitle_style': plan['settings']['subtitle_style'],
            'color': bool(plan['settings']['color'])}


# --------------------------------------------------------------------------- renderização

def keep_intervals(plan: dict, duration: float) -> list[tuple[float, float]] | None:
    """Trechos a manter (relativos ao corte) depois de remover as pausas habilitadas."""
    cuts = sorted((a['start'], a['end']) for a in plan['actions']
                  if a['type'] == 'cut_pause' and a.get('enabled', True))
    if not cuts:
        return None
    keep, pos = [], 0.0
    for a, b in cuts:
        if a > pos:
            keep.append((round(pos, 3), round(a, 3)))
        pos = max(pos, b)
    if pos < duration:
        keep.append((round(pos, 3), round(duration, 3)))
    return [k for k in keep if k[1] - k[0] > 0.05] or None


def _ramp(a: float, b: float) -> str:
    """0→1→0 com rampas suaves (smoothstep) entre a e b, em função de ``it``."""
    up = f"clip((it-{a:.3f})/{ZOOM_IN},0,1)"
    down = f"clip(({b:.3f}-it)/{ZOOM_OUT},0,1)"
    return f"({up}*{up}*(3-2*{up}))*({down}*{down}*(3-2*{down}))"


def zoom_expression(plan: dict, remap) -> str | None:
    """Expressão de zoom (1.0 = sem zoom) para o ``zoompan``, nos tempos já remapeados."""
    terms = []
    for act in plan['actions']:
        if not act.get('enabled', True) or act['type'] not in ('zoom', 'push'):
            continue
        a, b = remap(act['start']), remap(act['end'])
        if b - a < 0.3:
            continue
        amount = min(ZOOM_MAX, float(act['params']['amount']))
        if act['type'] == 'push':
            terms.append(f"{amount:.3f}*clip((it-{a:.3f})/{b - a:.3f},0,1)*between(it,{a:.3f},{b:.3f})")
        else:
            terms.append(f"{amount:.3f}*{_ramp(a, b)}")
    return f"1+{'+'.join(terms)}" if terms else None


def video_post_filters(plan: dict, size: tuple[int, int], fps: float, out_duration: float, remap) -> str:
    """Filtros aplicados depois do layout e antes da legenda: cor, zoom e fades."""
    s = plan['settings']
    parts = []
    color = s.get('color')
    if color:
        parts.append(f"eq=contrast={color.get('contrast', 1):.3f}:saturation={color.get('saturation', 1):.3f}")
        if color.get('vignette'):
            parts.append('vignette=angle=PI/5')
    zexpr = zoom_expression(plan, remap)
    if zexpr:
        W, H = size
        parts.append(f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps:g}")
    fade = float(s.get('video_fade') or 0)
    if fade > 0 and out_duration > fade * 3:
        parts.append(f'fade=t=in:st=0:d={fade:g},fade=t=out:st={out_duration - fade:.3f}:d={fade:g}')
    return (',' + ','.join(parts)) if parts else ''


def audio_filters(plan: dict, out_duration: float) -> str:
    s = plan['settings']
    parts = []
    if s.get('denoise'):
        parts.append('afftdn=nf=-25')
    fade = float(s.get('audio_fade') or 0)
    vfade = float(s.get('video_fade') or 0)
    fade = max(fade, vfade)
    if fade > 0 and out_duration > fade * 3:
        parts.append(f'afade=t=in:d={fade:g},afade=t=out:st={out_duration - fade:.3f}:d={fade:g}')
    return ','.join(parts)


def emphasis_starts(plan: dict) -> set[float]:
    return {round(a['start'], 2) for a in plan['actions'] if a['type'] == 'highlight' and a.get('enabled', True)}
