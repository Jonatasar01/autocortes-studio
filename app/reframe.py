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
    w, h = media.get('width') or 0, media.get('height') or 0
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


def speaker_targets(samples: list[dict]) -> list[tuple[float, float | None]]:
    """Para cada amostra, centro horizontal (0-1) do provável falante, ou None sem rosto."""
    import cv2
    import numpy as np
    tracks: list[dict] = []
    targets: list[tuple[float, float | None]] = []
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
            targets.append((s['t'], speaker['box'][0] + speaker['box'][2] / 2))
        else:
            targets.append((s['t'], None))
    return targets


def camera_path(targets: list[tuple[float, float | None]], crop_frac: float) -> list[tuple[float, float]]:
    """Câmera virtual: zona morta + suavização exponencial, limitada às bordas do quadro."""
    known = [x for _, x in targets if x is not None]
    if not known:
        return []
    half = crop_frac / 2
    clamp = lambda v: min(max(v, half), 1 - half)  # noqa: E731
    last = known[0]
    cam = clamp(last)
    path = []
    for t, x in targets:
        last = x if x is not None else last
        goal = clamp(last)
        if abs(goal - cam) > DEAD_ZONE:
            cam += SMOOTHING * (goal - cam)
        else:
            cam += SMOOTHING * 0.15 * (goal - cam)
        path.append((round(t, 3), round(cam, 4)))
    return path


def simplify(points: list[tuple[float, float]], tolerance: float = SIMPLIFY_TOLERANCE) -> list[tuple[float, float]]:
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
    while len(out) > MAX_POINTS_PER_SHOT:  # caminhos muito agitados: reduz a densidade
        out = out[::2] + ([out[-1]] if len(out) % 2 == 0 else [])
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

    w, h = media['width'], media['height']
    crop_frac = min(1.0, (h * out_size[0] / out_size[1]) / w)
    cuts = sorted({round(s - start, 3) for s in (scenes or []) if start < s < end} | set(histogram_cuts(samples)))
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= MIN_SHOT_SECONDS and duration - c >= MIN_SHOT_SECONDS:
            bounds.append(c)
    bounds.append(round(duration, 3))

    shots: list[dict] = []
    for a, b in zip(bounds, bounds[1:]):
        part = [s for s in samples if a <= s['t'] < b]
        targets = speaker_targets(part)
        seen = sum(1 for _, x in targets if x is not None)
        if not targets or seen / len(targets) < MIN_FACE_COVERAGE:
            layout, points = 'fit', []
        else:
            points = simplify(camera_path(targets, crop_frac))
            layout = 'fill'
            if points and points[0][0] > a:
                points.insert(0, (a, points[0][1]))
        if shots and shots[-1]['layout'] == layout == 'fit':
            shots[-1]['end'] = b
        else:
            shots.append({'start': a, 'end': b, 'layout': layout, 'points': points})
    if not any(s['layout'] == 'fill' for s in shots):
        return {'ok': False, 'shots': [], 'message': 'Nenhum rosto encontrado para seguir neste trecho; '
                'exportado com fundo desfocado.'}
    fit_seconds = sum(s['end'] - s['start'] for s in shots if s['layout'] == 'fit')
    message = 'Enquadramento seguindo o rosto de quem fala.'
    if fit_seconds >= 0.5:
        message += f' {fit_seconds:.0f} s sem rosto visível usam fundo desfocado.'
    return {'ok': True, 'shots': shots, 'crop_frac': round(crop_frac, 4), 'message': message}


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
