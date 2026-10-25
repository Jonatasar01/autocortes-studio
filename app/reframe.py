"""Enquadramento "Seguir rosto": recorte vertical que acompanha quem está falando.

Diferente de "Barras" e "Fundo desfocado", este modo CORTA as laterais da imagem. Por isso é opcional
e cai automaticamente para "Fundo desfocado" quando não há rosto suficiente para seguir.

Como funciona (equivalente simplificado ao reenquadramento de serviços como o Opus Clip):
1. Amostra o trecho a poucos quadros por segundo (só leitura, em baixa resolução).
2. Detecta rostos com os classificadores Haar do OpenCV (incluídos em ``app/data``; nada é baixado).
3. Liga as detecções entre quadros e estima quem fala pelo movimento da região da boca.
4. Move uma "câmera virtual" com zona morta e suavização, reiniciada a cada mudança de cena
   (cenas detectadas na análise e por diferença de histograma).
5. Cenas sem rosto suficiente usam o layout de fundo desfocado só naquele intervalo.
6. O caminho da câmera é simplificado e vira uma expressão de tempo do filtro ``crop`` do FFmpeg,
   mantendo a exportação 100% no FFmpeg (validação, cancelamento e progresso continuam iguais).

OpenCV é opcional (``requirements-ai.txt``). Sem ele, a exportação usa fundo desfocado e avisa.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from .errors import Cancelled, sanitize

log = logging.getLogger('autocortes.reframe')

DATA_DIR = Path(__file__).resolve().parent / 'data'
ANALYSIS_HEIGHT = 480        # resolução usada apenas para análise
SAMPLE_FPS = 5.0
SHOT_THRESHOLD = 0.45        # distância de histograma (Bhattacharyya) que indica corte de cena
MIN_FACE_COVERAGE = 0.3      # fração das amostras de uma cena com rosto para seguir o rosto
DEAD_ZONE = 0.06             # a câmera só se move se o alvo sair desta faixa (fração da largura)
SMOOTHING = 0.18             # fração da distância percorrida a cada amostra
SIMPLIFY_TOLERANCE = 0.004   # erro máximo (fração da largura) ao simplificar o caminho da câmera
MAX_POINTS_PER_SHOT = 40
MIN_SHOT_SECONDS = 0.6
# Segurança do rosto: a caixa do rosto, com margem, precisa caber inteira no recorte.
FACE_MARGIN_RATIO = 0.20     # margem lateral = 20% da largura do rosto (cabelo, movimento, erro do detector)
FACE_MIN_MARGIN = 0.015      # e pelo menos 1,5% da largura do quadro

_cascades: dict[str, object] = {}
_cascade_lock = threading.Lock()


def available() -> bool:
    try:
        import cv2
        import numpy  # noqa: F401
    except ImportError:
        return False
    # O OpenCV 5 tirou o CascadeClassifier do pacote principal: sem ele não há detecção.
    return hasattr(cv2, 'CascadeClassifier') and (DATA_DIR / 'haarcascade_frontalface_default.xml').is_file()


def applicable(media: dict, out_size: tuple[int, int]) -> bool:
    """Só faz sentido quando a saída é mais estreita que o vídeo (ex.: 16:9 → 9:16 ou 1:1)."""
    box = media.get('letterbox')
    w, h = (box['w'], box['h']) if box else (media.get('width') or 0, media.get('height') or 0)
    if not w or not h:
        return False
    return out_size[0] / out_size[1] < (w / h) * 0.92


# --------------------------------------------------------------------------- detecção

def _cascade(name: str):
    import cv2
    with _cascade_lock:
        if name not in _cascades:
            clf = cv2.CascadeClassifier(str(DATA_DIR / name))
            if clf.empty():
                raise RuntimeError(f'classificador {name} ausente ou inválido')
            _cascades[name] = clf
        return _cascades[name]


def detect_faces(gray) -> list[tuple[float, float, float, float]]:
    """Rostos como (x, y, largura, altura) em fração do quadro."""
    h, w = gray.shape[:2]
    side = max(20, h // 18)
    boxes = _cascade('haarcascade_frontalface_default.xml').detectMultiScale(
        gray, scaleFactor=1.08, minNeighbors=4, minSize=(side, side))
    if len(boxes) == 0 and (DATA_DIR / 'haarcascade_profileface.xml').is_file():
        boxes = _cascade('haarcascade_profileface.xml').detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=4, minSize=(side, side))
    return [(float(x) / w, float(y) / h, float(bw) / w, float(bh) / h) for (x, y, bw, bh) in boxes]


def sample(source: Path, start: float, end: float, *, cancel_event: threading.Event | None = None,
           on_progress: Callable[[float, str], None] | None = None) -> list[dict]:
    """Lê o trecho e devolve amostras {t (relativo ao início do corte), hist, gray, faces}."""
    import cv2
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError('não foi possível abrir o vídeo para análise de rostos')
    samples: list[dict] = []
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        next_t = start
        span = max(0.1, end - start)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            ok, frame = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t >= end:
                break
            if t + 1e-3 < next_t:
                continue
            next_t = max(next_t + 1 / SAMPLE_FPS, t)
            fh, fw = frame.shape[:2]
            scale = ANALYSIS_HEIGHT / fh if fh > ANALYSIS_HEIGHT else 1.0
            small = cv2.resize(frame, (max(2, int(fw * scale)), max(2, int(fh * scale))),
                               interpolation=cv2.INTER_AREA) if scale != 1.0 else frame
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            samples.append({'t': max(0.0, t - start), 'hist': hist, 'gray': gray, 'faces': detect_faces(gray)})
            if on_progress:
                on_progress(min(1.0, (t - start) / span), '')
    finally:
        cap.release()
    return samples


def histogram_cuts(samples: list[dict]) -> list[float]:
    import cv2
    cuts = []
    for prev, cur in zip(samples, samples[1:]):
        if cv2.compareHist(prev['hist'], cur['hist'], cv2.HISTCMP_BHATTACHARYYA) > SHOT_THRESHOLD:
            cuts.append(round((prev['t'] + cur['t']) / 2, 3))
    return cuts


# --------------------------------------------------------------------------- falante e câmera

def _mouth(gray, box):
    import cv2
    h, w = gray.shape[:2]
    x, y, bw, bh = box
    x0, x1 = int((x + bw * 0.25) * w), int((x + bw * 0.75) * w)
    y0, y1 = int((y + bh * 0.65) * h), int((y + bh * 0.95) * h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return cv2.resize(gray[y0:y1, x0:x1], (24, 12))


def speaker_targets(samples: list[dict]) -> list[tuple]:
    """Para cada amostra: ``(t, centro_x, caixa_do_falante, caixas_visíveis)``.

    Sem rosto: ``(t, None, None, [])``. As caixas são ``(x, y, largura, altura)`` em fração do quadro;
    elas são mantidas para o recorte poder garantir que o rosto inteiro fique visível.
    """
    import cv2
    import numpy as np
    tracks: list[dict] = []
    targets: list[tuple] = []
    for i, s in enumerate(samples):
        for box in s['faces']:
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            best, best_d = None, 0.15
            for tr in tracks:
                if tr['last'] == i or i - tr['last'] > 10:
                    continue
                tx, ty = tr['box'][0] + tr['box'][2] / 2, tr['box'][1] + tr['box'][3] / 2
                d = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = tr, d
            if best is None:
                best = {'box': box, 'activity': 0.0, 'mouth': None, 'last': i}
                tracks.append(best)
            mouth = _mouth(s['gray'], box)
            if best['mouth'] is not None and mouth is not None:
                motion = float(np.mean(cv2.absdiff(mouth, best['mouth'])))
                best['activity'] = 0.7 * best['activity'] + 0.3 * motion
            best.update(box=box, mouth=mouth, last=i)
        visible = [tr for tr in tracks if tr['last'] == i]
        if visible:
            speaker = max(visible, key=lambda tr: (round(tr['activity'], 1), tr['box'][2] * tr['box'][3]))
            targets.append((s['t'], speaker['box'][0] + speaker['box'][2] / 2, tuple(speaker['box']),
                            [tuple(tr['box']) for tr in visible]))
        else:
            targets.append((s['t'], None, None, []))
    return targets


FACE_MARGIN_STEPS = (FACE_MARGIN_RATIO, FACE_MARGIN_RATIO / 2, 0.0)   # margem preferida → mínima


def expanded_face_x(box: tuple[float, float, float, float],
                    margin_ratio: float = FACE_MARGIN_RATIO) -> tuple[float, float]:
    """Bordas horizontais do rosto com margem (fração da largura do quadro)."""
    x, _y, w, _h = box
    pad = max(FACE_MIN_MARGIN if margin_ratio > 0 else 0.0, w * margin_ratio)
    return max(0.0, x - pad), min(1.0, x + w + pad)


def safe_center_interval(box: tuple[float, float, float, float], crop_frac: float) -> tuple[float, float] | None:
    """Centros de recorte que mantêm o rosto inteiro dentro do recorte.

    ``crop_left <= face_left`` e ``crop_right >= face_right``  ⇒  ``right - C/2 <= centro <= left + C/2``,
    limitado às bordas do quadro. Tenta a margem preferida (20% da largura do rosto); em close-ups
    reduz a margem até zero, mas nunca aceita cortar o próprio rosto. ``None``: nem o rosto cabe.
    """
    for ratio in FACE_MARGIN_STEPS:
        left, right = expanded_face_x(box, ratio)
        lo = max(crop_frac / 2, right - crop_frac / 2)
        hi = min(1 - crop_frac / 2, left + crop_frac / 2)
        if lo <= hi + 1e-9:
            return lo, hi
    return None


def face_visibility(box: tuple[float, float, float, float], center: float, crop_frac: float) -> float:
    """Fração (0-1) da largura do rosto (sem margem) que fica dentro do recorte centrado em ``center``."""
    left, right = expanded_face_x(box, 0.0)
    c0, c1 = center - crop_frac / 2, center + crop_frac / 2
    inter = max(0.0, min(right, c1) - max(left, c0))
    return min(1.0, inter / max(1e-6, right - left))


def union_box(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    return x0, y0, x1 - x0, y1 - y0


def _sample_interval(target: tuple, crop_frac: float) -> tuple[float, float] | None | bool:
    """Intervalo seguro da amostra: todos os rostos visíveis se couberem juntos, senão o do falante.

    ``False``: amostra sem rosto. ``None``: há rosto, mas nenhum recorte o preserva.
    """
    box = target[2] if len(target) > 2 else None
    if box is None:
        return False
    faces = target[3] if len(target) > 3 else []
    if len(faces) > 1:
        both = safe_center_interval(union_box(list(faces)), crop_frac)
        if both is not None:
            return both
    return safe_center_interval(box, crop_frac)


def camera_path(targets: list[tuple], crop_frac: float) -> list[tuple[float, float]]:
    """Câmera virtual: zona morta + suavização exponencial, limitada às bordas do quadro.

    Quando a amostra traz a caixa do rosto, a segurança tem precedência sobre a suavização: o centro
    é sempre levado para dentro do intervalo em que o rosto (com margem) cabe inteiro no recorte.
    """
    known = [tg[1] for tg in targets if tg[1] is not None]
    if not known:
        return []
    half = crop_frac / 2
    clamp = lambda v: min(max(v, half), 1 - half)  # noqa: E731
    last = known[0]
    cam = clamp(last)
    path = []
    for tg in targets:
        t, x = tg[0], tg[1]
        last = x if x is not None else last
        goal = clamp(last)
        interval = _sample_interval(tg, crop_frac)
        if interval:
            goal = min(max(goal, interval[0]), interval[1])
        elif interval is None:
            cam = goal  # rosto maior que o recorte: centraliza já, sem atraso da suavização
        if abs(goal - cam) > DEAD_ZONE:
            cam += SMOOTHING * (goal - cam)
        else:
            cam += SMOOTHING * 0.15 * (goal - cam)
        if interval:
            cam = min(max(cam, interval[0]), interval[1])
        path.append((round(t, 3), round(cam, 4)))
    return path


def _center_at(points: list[tuple[float, float]], t: float) -> float:
    if t <= points[0][0]:
        return points[0][1]
    for (t0, x0), (t1, x1) in zip(points, points[1:]):
        if t0 <= t <= t1:
            return x0 + (x1 - x0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0)
    return points[-1][1]


def visibility_report(targets: list[tuple], points: list[tuple[float, float]], crop_frac: float) -> list[float]:
    """Visibilidade do rosto do falante em cada amostra com rosto, no caminho que será renderizado."""
    if not points:
        return []
    return [face_visibility(tg[2], _center_at(points, tg[0]), crop_frac)
            for tg in targets if len(tg) > 2 and tg[2] is not None]


def simplify(points: list[tuple[float, float]], tolerance: float = SIMPLIFY_TOLERANCE,
             max_points: int = MAX_POINTS_PER_SHOT) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker: mantém só os pontos necessários para o movimento da câmera."""
    if len(points) <= 2:
        return list(points)

    def rdp(lo: int, hi: int, keep: set[int]) -> None:
        (t0, x0), (t1, x1) = points[lo], points[hi]
        worst, idx = 0.0, -1
        for k in range(lo + 1, hi):
            t, x = points[k]
            expected = x0 + (x1 - x0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0)
            err = abs(x - expected)
            if err > worst:
                worst, idx = err, k
        if worst > tolerance and idx > 0:
            keep.add(idx)
            rdp(lo, idx, keep)
            rdp(idx, hi, keep)

    keep = {0, len(points) - 1}
    rdp(0, len(points) - 1, keep)
    out = [points[k] for k in sorted(keep)]
    while len(out) > max_points:  # caminhos muito agitados: reduz a densidade
        out = out[::2] + ([out[-1]] if len(out) % 2 == 0 else [])
    return out


MAX_SAFE_POINTS = 160   # teto do caminho verificado (a expressão do crop continua curta para o FFmpeg)


def safe_simplify(targets: list[tuple], raw: list[tuple[float, float]], crop_frac: float) -> list[tuple[float, float]]:
    """Simplifica o caminho só até onde nenhuma amostra perde visibilidade do rosto.

    Tenta tolerâncias cada vez menores; fica com o primeiro caminho em que cada amostra mantém a
    visibilidade do caminho bruto (tolerância de 1 ponto percentual).
    """
    reference = visibility_report(targets, raw, crop_frac)
    for tol in (SIMPLIFY_TOLERANCE, SIMPLIFY_TOLERANCE / 2, SIMPLIFY_TOLERANCE / 4, SIMPLIFY_TOLERANCE / 10, 0.0):
        points = simplify(raw, tolerance=tol, max_points=MAX_SAFE_POINTS)
        got = visibility_report(targets, points, crop_frac)
        if all(g >= r - 0.01 for g, r in zip(got, reference)):
            return points
    return simplify(raw, tolerance=0.0, max_points=10 ** 6)


def _split_unsafe(targets: list[tuple], a: float, b: float, crop_frac: float) -> list[tuple]:
    """Divide uma cena em trechos ``fill`` e ``fit``: rosto presente sem recorte seguro por pelo menos
    ``MIN_SHOT_SECONDS`` vira ``fit``; sequências curtas ficam no recorte (centrado no rosto)."""
    step = 1 / SAMPLE_FPS
    runs, current = [], None
    for tg in targets:
        bad = _sample_interval(tg, crop_frac) is None
        if bad:
            if current and tg[0] - current[1] <= step * 1.5:
                current[1] = tg[0]
            else:
                current = [tg[0], tg[0]]
                runs.append(current)
    runs = [(max(a, r0 - step / 2), min(b, r1 + step / 2)) for r0, r1 in runs if r1 - r0 + step >= MIN_SHOT_SECONDS]
    out, cursor = [], a
    for r0, r1 in runs:
        if r0 - cursor >= MIN_SHOT_SECONDS:
            out.append((cursor, r0, 'fill', [tg for tg in targets if cursor <= tg[0] < r0]))
        elif out or r0 > cursor:
            r0 = cursor  # pedaço seguro curto demais entre trechos inseguros: junta ao fundo desfocado
        out.append((r0, r1, 'fit', []))
        cursor = r1
    if b - cursor >= MIN_SHOT_SECONDS or not out:
        out.append((cursor, b, 'fill', [tg for tg in targets if cursor <= tg[0] < b]))
    elif out:
        last = out[-1]
        out[-1] = (last[0], b, last[2], last[3] if last[2] == 'fit' else
                   [tg for tg in targets if last[0] <= tg[0] < b])
    return out


def plan(source: Path, media: dict, start: float, end: float, out_size: tuple[int, int], *,
         scenes: list[float] | None = None, cancel_event: threading.Event | None = None,
         on_progress: Callable[[float, str], None] | None = None) -> dict:
    """Plano de enquadramento do corte. Tempos relativos ao início do corte.

    Retorna ``{'ok': bool, 'message': str, 'shots': [{'start','end','layout','points'}], 'crop_frac'}``.
    ``ok`` é falso quando não dá para seguir rostos (sem OpenCV, sem rosto ou formato não aplicável).
    """
    if not applicable(media, out_size):
        return {'ok': False, 'shots': [], 'message': 'O formato escolhido já mostra a largura inteira do vídeo; '
                'usado fundo desfocado em vez de seguir rosto.'}
    if not available():
        return {'ok': False, 'shots': [], 'message': 'Seguir rosto requer OpenCV (pip install -r '
                'requirements-ai.txt). Exportado com fundo desfocado.'}
    duration = max(0.1, end - start)
    try:
        samples = sample(source, start, end, cancel_event=cancel_event, on_progress=on_progress)
    except Cancelled:
        raise
    except Exception as exc:
        log.warning('Falha na análise de rostos: %s', sanitize(exc, 200))
        return {'ok': False, 'shots': [], 'message': 'Não foi possível analisar os rostos deste trecho; '
                'exportado com fundo desfocado.'}
    if not samples:
        return {'ok': False, 'shots': [], 'message': 'Nenhum quadro lido para seguir rosto; exportado com fundo desfocado.'}

    box = media.get('letterbox')
    w, h = (box['w'], box['h']) if box else (media['width'], media['height'])
    crop_frac = min(1.0, (h * out_size[0] / out_size[1]) / w)
    cuts = sorted({round(s - start, 3) for s in (scenes or []) if start < s < end} | set(histogram_cuts(samples)))
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= MIN_SHOT_SECONDS and duration - c >= MIN_SHOT_SECONDS:
            bounds.append(c)
    bounds.append(round(duration, 3))

    shots: list[dict] = []
    visibility: list[float] = []
    unsafe_shots = 0

    def add(a: float, b: float, layout: str, points: list) -> None:
        if b - a <= 1e-3:
            return
        if shots and shots[-1]['layout'] == layout == 'fit':
            shots[-1]['end'] = b
        else:
            shots.append({'start': a, 'end': b, 'layout': layout, 'points': points})

    for a, b in zip(bounds, bounds[1:]):
        part = [s for s in samples if a <= s['t'] < b]
        targets = _to_visible(speaker_targets(part), media, box)
        seen = sum(1 for tg in targets if tg[1] is not None)
        if not targets or seen / len(targets) < MIN_FACE_COVERAGE:
            add(a, b, 'fit', [])
            continue
        for sa, sb, layout, sub in _split_unsafe(targets, a, b, crop_frac):
            if layout == 'fit':
                # Há rosto, mas nenhum recorte vertical o preserva (close extremo, duas pessoas afastadas):
                # quadro inteiro com fundo desfocado só neste trecho, em vez de cortar a pessoa.
                unsafe_shots += 1
                add(sa, sb, 'fit', [])
                continue
            raw = camera_path(sub, crop_frac)
            if not raw:
                add(sa, sb, 'fit', [])
                continue
            # A simplificação não pode tirar o rosto do recorte (verificada amostra a amostra).
            points = safe_simplify(sub, raw, crop_frac)
            visibility += visibility_report(sub, points, crop_frac)
            if points and points[0][0] > sa:
                points.insert(0, (sa, points[0][1]))
            add(sa, sb, 'fill', points)
    if not any(s['layout'] == 'fill' for s in shots):
        if unsafe_shots:
            return {'ok': False, 'shots': [], 'message': 'Os rostos deste trecho não cabem inteiros no recorte '
                    'vertical; exportado com fundo desfocado para não cortar ninguém.'}
        return {'ok': False, 'shots': [], 'message': 'Nenhum rosto encontrado para seguir neste trecho; '
                'exportado com fundo desfocado.'}
    fit_seconds = sum(s['end'] - s['start'] for s in shots if s['layout'] == 'fit')
    message = 'Enquadramento seguindo o rosto de quem fala.'
    if fit_seconds >= 0.5:
        message += f' {fit_seconds:.0f} s sem rosto seguro para recortar usam fundo desfocado.'
    if unsafe_shots:
        message += f' {unsafe_shots} cena(s) com rosto que não cabe no recorte vertical ficaram em quadro inteiro.'
    ordered = sorted(visibility)
    metrics = {'face_samples': len(ordered),
               'face_visibility_min': round(ordered[0], 3) if ordered else None,
               'face_visibility_p05': round(ordered[int(len(ordered) * 0.05)], 3) if ordered else None,
               'unsafe_samples': sum(1 for v in ordered if v < 0.9),
               'unsafe_shots': unsafe_shots, 'fit_seconds': round(fit_seconds, 2)}
    return {'ok': True, 'shots': shots, 'crop_frac': round(crop_frac, 4), 'message': message, 'metrics': metrics}


def _to_visible(targets: list[tuple], media: dict, box: dict | None) -> list[tuple]:
    """Centro e caixas do falante (fração do quadro inteiro) → fração da área sem faixas pretas."""
    if not box:
        return targets
    W = media['width']

    def fx(v: float) -> float:
        return (v * W - box['x']) / box['w']

    def fbox(b):
        return (fx(b[0]), b[1], b[2] * W / box['w'], b[3]) if b else b

    out = []
    for tg in targets:
        t, x = tg[0], tg[1]
        nx = None if x is None else min(1.0, max(0.0, fx(x)))
        if len(tg) > 2:
            out.append((t, nx, fbox(tg[2]), [fbox(b) for b in tg[3]]))
        else:
            out.append((t, nx))
    return out


# --------------------------------------------------------------------------- FFmpeg

def crop_x_expression(shots: list[dict]) -> str:
    """Centro do recorte (fração da largura) como expressão de ``t`` do FFmpeg, linear por partes.

    Usa soma de termos com ``between`` (sem aninhamento), então funciona com dezenas de pontos.
    """
    terms: list[str] = []
    fill = [s for s in shots if s['layout'] == 'fill' and s['points']]
    for shot in fill:
        pts = shot['points']
        for (t0, x0), (t1, x1) in zip(pts, pts[1:]):
            if t1 <= t0:
                continue
            if abs(x1 - x0) < 1e-4:
                terms.append(f'gte(t,{t0:.3f})*lt(t,{t1:.3f})*{x0:.4f}')
            else:
                slope = (x1 - x0) / (t1 - t0)
                terms.append(f'gte(t,{t0:.3f})*lt(t,{t1:.3f})*({x0:.4f}+{slope:.6f}*(t-{t0:.3f}))')
        last_t, last_x = pts[-1]
        terms.append(f'gte(t,{last_t:.3f})*lt(t,{shot["end"]:.3f})*{last_x:.4f}')
    # fora das cenas com rosto (ou antes do primeiro ponto) a câmera fica no centro: soma dos indicadores = 0
    covered = '+'.join(f'gte(t,{s["points"][0][0]:.3f})*lt(t,{s["end"]:.3f})' for s in fill) or '0'
    terms.append(f'(1-min(1,{covered}))*0.5')
    return '+'.join(terms)


def fit_enable_expression(shots: list[dict]) -> str | None:
    spans = [s for s in shots if s['layout'] == 'fit']
    if not spans:
        return None
    return '+'.join(f'between(t,{s["start"]:.3f},{s["end"]:.3f})' for s in spans)
