"""Enquadramento "Seguir rosto": recorte vertical que acompanha quem está falando.

Diferente de "Barras" e "Fundo desfocado", este modo CORTA as laterais da imagem. Por isso é opcional
e cai automaticamente para "Fundo desfocado" quando não há rosto suficiente para seguir.

Como funciona (equivalente simplificado ao reenquadramento de serviços como o Opus Clip):
1. Amostra o trecho a poucos quadros por segundo (só leitura, em baixa resolução).
2. Detecta rostos com o YuNet (rede do ``cv2.FaceDetectorYN``, modelo de 230 KB em ``app/data``, licença
   MIT), que também dá olhos e cantos da boca. Sem ``FaceDetectorYN``, usa os classificadores Haar, que
   confundem texturas (papel de parede, mosaicos) com rostos.
3. Liga as detecções entre quadros, descarta as que "piscam" (aparecem em poucos quadros da cena) e
   estima quem fala pelo movimento da boca MENOS o movimento da testa: numa textura ou num movimento
   de câmera tudo se mexe igual, e a diferença fica perto de zero.
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

from . import media
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
PLAN_VERSION = 8   # muda quando o algoritmo do plano muda (invalida os planos em cache)
YUNET_MODEL = 'face_detection_yunet_2023mar.onnx'
YUNET_SCORE = 0.7            # confiança mínima do YuNet
MIN_TRACK_HITS = 3           # um rosto precisa aparecer em 3 amostras (0,6 s); falsos positivos piscam
# (absoluto, não fração da cena: uma troca de câmera que a análise não viu não pode apagar um close curto)
SPEAKER_MIN_WIDTH = 0.45     # candidatos a falante: rostos com pelo menos 45% da largura do maior visível
SPEAKER_SWITCH = 1.5         # vantagem para trocar de falante dentro da cena (evita vaivém)
SPEAKER_MIN_HOLD = 10        # e só depois de 10 amostras (2 s) no falante atual: troca a cada 1 s cansa
STATIC_FACE_MOTION = 0.05    # rosto que oscila menos que 5% da própria largura: imagem/ícone, não pessoa
SPEAKER_MOTION_WEIGHT = 12.0 # peso do movimento do rosto na escolha de quem fala
FACECAM_SAMPLES = 40         # quadros usados para achar a moldura da webcam
# Cena principal (bancada dos streamers numa transmissão que alterna com campo, túnel, jogo...):
SET_CLUSTERS = 4             # grupos de aparência (histograma de cor) procurados no corte
SET_MERGE = 0.35             # grupos mais próximos que isto (Bhattacharyya) são a mesma cena (outro ângulo)
SET_OTHER = 0.45             # uma cena "diferente" fica a pelo menos esta distância da principal…
SET_OTHER_SHARE = 0.10       # …e ocupa ao menos 10% do corte; senão não há o que separar
SET_MAIN_SHARE = 0.20        # a cena principal ocupa ao menos 20% do corte
SET_MIN_RUN = 1.0            # trechos mais curtos que 1 s herdam a cena vizinha (transições, flashes)
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


def yunet_available() -> bool:
    try:
        import cv2
    except ImportError:
        return False
    return hasattr(cv2, 'FaceDetectorYN') and (DATA_DIR / YUNET_MODEL).is_file()


def _yunet(size: tuple[int, int]):
    """Um detector por análise (o FaceDetectorYN guarda estado e não é seguro entre threads)."""
    import cv2
    return cv2.FaceDetectorYN.create(str(DATA_DIR / YUNET_MODEL), '', size, YUNET_SCORE, 0.3, 50)


def detect_faces_yunet(frame, detector) -> tuple[list[tuple], list[tuple]]:
    """Rostos ``(x, y, largura, altura)`` e marcos ``(olho_d, olho_e, nariz, boca_d, boca_e)``, em fração."""
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    _, found = detector.detect(frame)
    boxes, marks = [], []
    for f in [] if found is None else found:
        x, y, bw, bh = (float(v) for v in f[:4])
        x0, y0 = max(0.0, x), max(0.0, y)
        boxes.append((x0 / w, y0 / h, (min(w, x + bw) - x0) / w, (min(h, y + bh) - y0) / h))
        marks.append(tuple((float(f[4 + 2 * k]) / w, float(f[5 + 2 * k]) / h) for k in range(5)))
    return boxes, marks


def sample(source: Path, start: float, end: float, *, cancel_event: threading.Event | None = None,
           on_progress: Callable[[float, str], None] | None = None) -> list[dict]:
    """Lê o trecho e devolve amostras {t (relativo ao início do corte), hist, gray, faces}."""
    import cv2
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError('não foi possível abrir o vídeo para análise de rostos')
    samples: list[dict] = []
    detector = None
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
            entry = {'t': max(0.0, t - start), 'hist': hist, 'gray': gray}
            if yunet_available():
                detector = detector or _yunet((small.shape[1], small.shape[0]))
                entry['faces'], entry['marks'] = detect_faces_yunet(small, detector)
            else:
                entry['faces'] = detect_faces(gray)
            samples.append(entry)
            if on_progress:
                on_progress(min(1.0, (t - start) / span), '')
    finally:
        cap.release()
    return samples


def main_set_mask(samples: list[dict]) -> list[bool] | None:
    """Quais amostras são da cena principal: a bancada dos streamers numa transmissão que alterna com campo,
    túnel ou jogo em tela cheia. ``None`` quando o corte não tem cenas bem diferentes entre si.

    Agrupa as amostras pelo histograma de cor (as transições dessas transmissões são cortinas e fusões que a
    detecção de corte não pega) e escolhe o grupo com mais tempo de tela × rosto maior: na bancada os rostos são
    close (8–11% da largura, medido), no campo são pequenos (5–6%). Grupos de cor parecida são outro ângulo da
    mesma cena.
    """
    import cv2
    import numpy as np
    n = len(samples)
    if n < 5 * SET_CLUSTERS:
        return None
    h = np.array([s['hist'].flatten() for s in samples], dtype=np.float32)
    h /= h.sum(axis=1, keepdims=True) + 1e-9
    # k-means com início determinístico (ponto mais distante): mesmo corte, mesmo resultado.
    centers = [h[0]]
    for _ in range(SET_CLUSTERS - 1):
        dist = np.min([((h - c) ** 2).sum(axis=1) for c in centers], axis=0)
        centers.append(h[int(dist.argmax())])
    centers = np.array(centers)
    for _ in range(25):
        labels = ((h[:, None, :] - centers[None]) ** 2).sum(axis=2).argmin(axis=1)
        centers = np.array([h[labels == j].mean(axis=0) if (labels == j).any() else centers[j]
                            for j in range(SET_CLUSTERS)])
    bh = lambda a, b: cv2.compareHist(a.astype(np.float32), b.astype(np.float32), cv2.HISTCMP_BHATTACHARYYA)  # noqa: E731
    biggest = np.array([max((f[2] for f in s['faces']), default=0.0) for s in samples])
    score = [((labels == j).mean() * float(np.median(biggest[labels == j]))) if (labels == j).any() else -1
             for j in range(SET_CLUSTERS)]
    main = int(np.argmax(score))
    same = [j for j in range(SET_CLUSTERS) if bh(centers[j], centers[main]) < SET_MERGE]
    mask = np.isin(labels, same)
    other = [j for j in range(SET_CLUSTERS) if bh(centers[j], centers[main]) >= SET_OTHER]
    if mask.mean() < SET_MAIN_SHARE or np.isin(labels, other).mean() < SET_OTHER_SHARE:
        return None
    # Trechos curtos herdam a cena de antes (transições).
    run = max(1, round(SET_MIN_RUN * SAMPLE_FPS))
    out, k = mask.tolist(), 0
    while k < n:
        e = k
        while e < n and out[e] == out[k]:
            e += 1
        if e - k < run and k > 0:
            for i in range(k, e):
                out[i] = out[k - 1]
        k = e
    return out


def histogram_cuts(samples: list[dict]) -> list[float]:
    import cv2
    cuts = []
    for prev, cur in zip(samples, samples[1:]):
        if cv2.compareHist(prev['hist'], cur['hist'], cv2.HISTCMP_BHATTACHARYYA) > SHOT_THRESHOLD:
            cuts.append(round((prev['t'] + cur['t']) / 2, 3))
    return cuts


# --------------------------------------------------------------------------- falante e câmera

def _patch(gray, rect):
    import cv2
    h, w = gray.shape[:2]
    x, y, rw, rh = rect
    x0, x1 = max(0, int(x * w)), min(w, int((x + rw) * w))
    y0, y1 = max(0, int(y * h)), min(h, int((y + rh) * h))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return cv2.resize(gray[y0:y1, x0:x1], (24, 12))


def face_regions(box, marks=None) -> tuple[tuple, tuple]:
    """Retângulos ``(boca, testa)`` em fração do quadro; com marcos do YuNet ficam presos à boca e aos olhos."""
    x, y, bw, bh = box
    if marks:
        (ex0, ey0), (ex1, ey1), _nose, (mx0, my0), (mx1, my1) = marks
        mw = max(abs(mx1 - mx0), bw * 0.2)
        mx, my = (mx0 + mx1) / 2, (my0 + my1) / 2
        ew = max(abs(ex1 - ex0), bw * 0.3)
        ex, ey = (ex0 + ex1) / 2, (ey0 + ey1) / 2
        return (mx - mw * 0.8, my - mw * 0.45, mw * 1.6, mw), (ex - ew * 0.8, ey - ew * 0.75, ew * 1.6, ew * 0.45)
    return (x + bw * 0.25, y + bh * 0.65, bw * 0.5, bh * 0.3), (x + bw * 0.25, y + bh * 0.08, bw * 0.5, bh * 0.2)


def _assign_tracks(samples: list[dict]) -> list[list[int]]:
    """Liga as detecções entre amostras pela distância dos centros; devolve o id da trilha de cada rosto."""
    last_box: dict[int, tuple] = {}
    last_seen: dict[int, int] = {}
    ids: list[list[int]] = []
    for i, s in enumerate(samples):
        row, taken = [], set()
        for box in s['faces']:
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            best, best_d = None, 0.15
            for tid, tb in last_box.items():
                if tid in taken or i - last_seen[tid] > 10:
                    continue
                d = ((cx - tb[0] - tb[2] / 2) ** 2 + (cy - tb[1] - tb[3] / 2) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = tid, d
            if best is None:
                best = len(last_box)
            taken.add(best)
            last_box[best], last_seen[best] = box, i
            row.append(best)
        ids.append(row)
    return ids


def _track_motion(samples: list[dict], ids: list[list[int]]) -> tuple[dict[int, float], dict[int, float]]:
    """``(movimento, largura mediana)`` de cada rosto da cena.

    Movimento: desvio do centro e do tamanho da caixa, em largura do próprio rosto. Pessoa real (mesmo
    parada, falando) passa de 5%; ícone ou retrato da interface de um jogo fica abaixo de 3%.
    """
    import numpy as np
    boxes: dict[int, list] = {}
    for s, row in zip(samples, ids):
        for box, tid in zip(s['faces'], row):
            boxes.setdefault(tid, []).append(box)
    motion, widths = {}, {}
    for tid, bs in boxes.items():
        a = np.asarray(bs, dtype=float)
        w = max(1e-6, float(a[:, 2].mean()))
        cx, cy = a[:, 0] + a[:, 2] / 2, a[:, 1] + a[:, 3] / 2
        motion[tid] = float(np.hypot(np.hypot(cx.std(), cy.std()), a[:, 2].std()) / w)
        widths[tid] = float(np.median(a[:, 2]))
    return motion, widths


def _speaking_score(activity: float, motion: float) -> float:
    """Indício de que o rosto é de quem fala: boca ativa (0–10+) e movimento do rosto (0,35 → +2,8)."""
    return activity + SPEAKER_MOTION_WEIGHT * motion


def speaker_targets(samples: list[dict]) -> list[tuple]:
    """Para cada amostra: ``(t, centro_x, caixa_do_falante, caixas_visíveis)``.

    Sem rosto: ``(t, None, None, [])``. As caixas são ``(x, y, largura, altura)`` em fração do quadro;
    elas são mantidas para o recorte poder garantir que o rosto inteiro fique visível.

    Trilhas que aparecem em menos de ``MIN_TRACK_HITS`` amostras são descartadas (detecções
    falsas em texturas piscam). Quem fala é quem tem mais movimento na boca do que na testa.
    """
    import cv2
    import numpy as np
    ids = _assign_tracks(samples)
    hits: dict[int, int] = {}
    for row in ids:
        for tid in row:
            hits[tid] = hits.get(tid, 0) + 1
    keep_min = min(MIN_TRACK_HITS, len(samples))
    motion, widths = _track_motion(samples, ids)
    # Rostos parados (ícones e retratos da interface de um jogo, fotos, pôsteres) não falam. Só saem da disputa
    # quando há algum rosto que se mexe na cena: num podcast com pessoas bem paradas, nada é descartado.
    alive = {t for t, m in motion.items() if hits[t] >= keep_min and m >= STATIC_FACE_MOTION}
    static = {t for t, m in motion.items() if m < STATIC_FACE_MOTION and hits[t] >= MIN_TRACK_HITS} if alive else set()
    state: dict[int, dict] = {}
    speaker_id, held = None, 0
    targets: list[tuple] = []
    for s, row in zip(samples, ids):
        marks = s.get('marks') or [None] * len(s['faces'])
        visible = []
        for box, mark, tid in zip(s['faces'], marks, row):
            if hits[tid] < keep_min or tid in static:
                continue
            tr = state.setdefault(tid, {'activity': 0.0, 'mouth': None, 'ref': None})
            mouth_rect, ref_rect = face_regions(box, mark)
            mouth, ref = _patch(s['gray'], mouth_rect), _patch(s['gray'], ref_rect)
            if tr['mouth'] is not None and mouth is not None and tr['ref'] is not None and ref is not None:
                moved = float(np.mean(cv2.absdiff(mouth, tr['mouth'])))
                still = float(np.mean(cv2.absdiff(ref, tr['ref'])))
                tr['activity'] = 0.7 * tr['activity'] + 0.3 * max(0.0, moved - still)
            tr.update(box=box, mouth=mouth, ref=ref)
            visible.append(tid)
        if not visible:
            targets.append((s['t'], None, None, []))
            continue
        # Largura típica de cada rosto na cena (mediana): virar a cabeça não tira a pessoa da disputa.
        widest = max(widths[t] for t in visible)
        candidates = [t for t in visible if widths[t] >= SPEAKER_MIN_WIDTH * widest]
        # Boca ativa + quanto o rosto se mexe: personagem de jogo ou figura animada mexe pouco a cabeça; pessoa
        # real falando mexe muito. Empate (ex.: duas pessoas paradas e caladas): o maior rosto.
        best = max(candidates, key=lambda t: (round(_speaking_score(state[t]['activity'], motion.get(t, 0.0)), 1),
                                              state[t]['box'][2] * state[t]['box'][3]))
        if speaker_id in candidates and best != speaker_id and (
                held < SPEAKER_MIN_HOLD
                or _speaking_score(state[best]['activity'], motion.get(best, 0.0))
                < _speaking_score(state[speaker_id]['activity'], motion.get(speaker_id, 0.0)) + SPEAKER_SWITCH):
            best = speaker_id   # troca de falante só com vantagem clara e depois de um tempo mínimo
        held = held + 1 if best == speaker_id else 1
        speaker_id = best
        box = state[best]['box']
        targets.append((s['t'], box[0] + box[2] / 2, tuple(box), [tuple(state[t]['box']) for t in visible]))
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
    prev_faces: list = []
    prev_x = None
    for tg in targets:
        t, x = tg[0], tg[1]
        last = x if x is not None else last
        goal = clamp(last)
        faces = tg[3] if len(tg) > 3 else []
        interval = _sample_interval(tg, crop_frac)
        if interval:
            goal = min(max(goal, interval[0]), interval[1])
        elif interval is None:
            cam = goal  # rosto maior que o recorte: centraliza já, sem atraso da suavização
        cut = (bool(faces and prev_faces and _new_shot(prev_faces, faces))  # troca de câmera não detectada
               or (x is not None and prev_x is not None and abs(x - prev_x) > crop_frac))  # falante longe
        if cut and path and abs(goal - path[-1][1]) > DEAD_ZONE:
            # Corte seco, como um editor faria: mantém a posição antiga até o instante da troca,
            # em vez de deslizar pelo cenário entre as duas pessoas.
            path.append((round(t - 0.001, 3), path[-1][1]))
            cam = goal
        prev_x = x if x is not None else prev_x
        if abs(goal - cam) > DEAD_ZONE:
            cam += SMOOTHING * (goal - cam)
        else:
            cam += SMOOTHING * 0.15 * (goal - cam)
        if interval:
            cam = min(max(cam, interval[0]), interval[1])
        if faces:
            prev_faces = faces
        path.append((round(t, 3), round(cam, 4)))
    return path


def _new_shot(before: list, after: list, radius: float = 0.15) -> bool:
    """Nenhum rosto atual perto de um rosto da amostra anterior: outro plano, não movimento de pessoa."""
    def center(b):
        return b[0] + b[2] / 2, b[1] + b[3] / 2
    return all(min(((center(a)[0] - center(b)[0]) ** 2 + (center(a)[1] - center(b)[1]) ** 2) ** 0.5
                   for b in before) > radius for a in after)


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
         on_progress: Callable[[float, str], None] | None = None, main_set_only: bool = False) -> dict:
    """Plano de enquadramento do corte. Tempos relativos ao início do corte.

    ``main_set_only`` ("só o streamer" sem janela de webcam): segue rostos só nas amostras da cena principal
    (``main_set_mask``, a bancada); o resto do corte usa quadro inteiro com fundo desfocado.

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
    facecam = detect_facecam(samples, speaker_targets(samples))
    main_mask = main_set_mask(samples) if main_set_only and not facecam else None
    if main_mask:   # as trocas de cena principal ↔ outras viram limites de cena
        flips = [round((a['t'] + b['t']) / 2, 3) for a, b, x, y in
                 zip(samples, samples[1:], main_mask, main_mask[1:]) if x != y]
        cuts = sorted(set(cuts) | set(flips))
        in_main = {id(s): m for s, m in zip(samples, main_mask)}
    # Fora da bancada, a transmissão costuma mostrar os streamers numa janela no canto: ela vira o enquadramento.
    pip = detect_pip([s for s, m in zip(samples, main_mask) if not m]) if main_mask else None
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
        if shots and shots[-1]['layout'] == layout and layout in ('fit', 'pip'):
            shots[-1]['end'] = b
        else:
            shots.append({'start': a, 'end': b, 'layout': layout, 'points': points})

    other_seconds = pip_seconds = 0.0
    for a, b in zip(bounds, bounds[1:]):
        part = [s for s in samples if a <= s['t'] < b]
        if main_mask and part and sum(in_main[id(s)] for s in part) < len(part) / 2:
            other_seconds += b - a   # fora da bancada (campo, túnel, jogo)
            if pip and pip_visible(part, pip):
                pip_seconds += b - a   # a janela com os streamers, ampliada
                add(a, b, 'pip', [])
            else:                      # sem a janela: mostra a cena inteira
                add(a, b, 'fit', [])
            continue
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
    if main_mask and pip_seconds:
        message = (f'Rostos seguidos só na cena principal (bancada); em {pip_seconds:.0f} s de outras cenas, a janela '
                   'com os streamers foi ampliada')
        rest = other_seconds - pip_seconds
        message += (f'; {rest:.0f} s sem a janela em quadro inteiro com fundo desfocado.' if rest >= 0.5 else '.')
    elif main_mask:
        message = (f'Rostos seguidos só na cena principal (bancada); {other_seconds:.0f} s de outras cenas '
                   '(campo, jogo...) em quadro inteiro com fundo desfocado.')
    elif fit_seconds >= 0.5:
        message += f' {fit_seconds:.0f} s sem rosto seguro para recortar usam fundo desfocado.'
    if unsafe_shots:
        message += f' {unsafe_shots} cena(s) com rosto que não cabe no recorte vertical ficaram em quadro inteiro.'
    ordered = sorted(visibility)
    metrics = {'face_samples': len(ordered),
               'face_visibility_min': round(ordered[0], 3) if ordered else None,
               'face_visibility_p05': round(ordered[int(len(ordered) * 0.05)], 3) if ordered else None,
               'unsafe_samples': sum(1 for v in ordered if v < 0.9),
               'unsafe_shots': unsafe_shots, 'fit_seconds': round(fit_seconds, 2),
               'other_scene_seconds': round(other_seconds, 2) if main_mask else None,
               'pip_seconds': round(pip_seconds, 2) if main_mask else None}
    return {'ok': True, 'shots': shots, 'crop_frac': round(crop_frac, 4), 'message': message, 'metrics': metrics,
            'facecam': _visible_rect(facecam, media, box), 'main_set_only': bool(main_mask),
            'pip': (_visible_rect(pip_inner(pip, samples[0]['gray'].shape[::-1]), media, box)
                    if pip_seconds else None)}


def _visible_rect(rect, media: dict, box: dict | None):
    """Retângulo em fração do quadro inteiro → fração da área sem faixas pretas."""
    if not rect or not box:
        return list(rect) if rect else None
    W, H = media['width'], media['height']
    x0, y0 = (rect[0] * W - box['x']) / box['w'], (rect[1] * H - box['y']) / box['h']
    x1, y1 = ((rect[0] + rect[2]) * W - box['x']) / box['w'], ((rect[1] + rect[3]) * H - box['y']) / box['h']
    x0, y0, x1, y1 = (min(1.0, max(0.0, v)) for v in (x0, y0, x1, y1))
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)] if x1 > x0 and y1 > y0 else None


def detect_facecam(samples: list[dict], targets: list[tuple]) -> tuple[float, float, float, float] | None:
    """Janela da webcam (``x, y, largura, altura`` em fração do quadro) em volta do rosto real, ou ``None``.

    Lives de jogo põem a câmera do streamer num retângulo sobre o jogo. A borda dessa janela é uma linha reta,
    contínua e no mesmo lugar em todos os quadros, enquanto o jogo muda: procura, a partir do rosto, a borda
    persistente mais próxima de cada lado (lados medidos na altura do rosto; topo e base, na largura achada).
    Lado sem borda (janela encostada na margem da tela) vai até a margem.
    """
    import cv2
    import numpy as np
    # A webcam fica numa lateral (esquerda ou direita): só rostos ali servem de ponto de partida. Um rosto no meio
    # da tela é do jogo ou da transmissão (jogadores, personagens), não a câmera do streamer.
    boxes = _lateral_boxes([tg[2] for tg in targets if len(tg) > 2 and tg[2]])
    if len(boxes) < 3:   # quem fala está no meio: procura os rostos das laterais em todas as amostras
        boxes = _lateral_boxes([tuple(f[:4]) for s in samples for f in s.get('faces') or []])
    if len(boxes) < 3:
        return None
    fx, fy, fw, fh = (float(v) for v in np.median(np.asarray(boxes), axis=0))
    picked = samples[::max(1, len(samples) // FACECAM_SAMPLES)]
    H, W = picked[0]['gray'].shape
    kernel = np.ones((3, 3), np.uint8)
    edges = np.mean([cv2.dilate(cv2.Canny(s['gray'], 60, 160), kernel) > 0 for s in picked], axis=0)
    persist = edges >= 0.8
    fl, ft, fr, fb = int(fx * W), int(fy * H), int((fx + fw) * W), int((fy + fh) * H)

    def scan(line, positions, need):
        for i in positions:
            if line(i).mean() >= need:
                return i
        return None

    y0, y1 = max(0, ft - (fb - ft)), min(H, fb + (fb - ft))
    right = scan(lambda x: persist[y0:y1, x], range(fr + 2, W), 0.85)
    left = scan(lambda x: persist[y0:y1, x], range(fl - 2, -1, -1), 0.85)
    L, R = left if left is not None else 0, right if right is not None else W - 1
    row = lambda y: persist[y, L + 3:max(L + 4, R - 3)]  # noqa: E731
    top = scan(row, range(ft - 2, -1, -1), 0.6)
    if top is None:
        top = scan(row, range(ft - 2, -1, -1), 0.45)
    bottom = scan(row, range(fb + 2, H), 0.6)
    T, B = top if top is not None else 0, bottom if bottom is not None else H - 1
    if sum(v is not None for v in (left, right, top, bottom)) < 2:
        return None   # sem moldura: não é uma janela de webcam
    rect = (L / W, T / H, (R - L) / W, (B - T) / H)
    face_area, area = fw * fh, rect[2] * rect[3]
    aspect = (rect[2] * W) / max(1.0, rect[3] * H)
    if not (face_area * 3 <= area <= 0.6 and 0.6 <= aspect <= 2.6):
        return None   # grande demais (a "janela" é a tela) ou formato estranho
    if rect[0] < FACECAM_SIDE_MAX < rect[0] + rect[2] and rect[0] < 1 - FACECAM_SIDE_MAX < rect[0] + rect[2]:
        return None   # atravessa o meio da tela: não é a janela lateral da webcam
    return tuple(round(v, 4) for v in rect)


FACECAM_SIDE_MAX = 0.42   # centro do rosto da webcam até 42% da largura (esquerda) ou a partir de 58% (direita)


def _lateral_boxes(boxes: list) -> list:
    """Caixas de rosto do lado (esquerdo ou direito) com mais ocorrências; nenhuma se só houver rostos no meio."""
    left = [b for b in boxes if b[0] + b[2] / 2 <= FACECAM_SIDE_MAX]
    right = [b for b in boxes if b[0] + b[2] / 2 >= 1 - FACECAM_SIDE_MAX]
    return left if len(left) >= len(right) else right


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

    Soma de termos indicadores agrupada em árvore (``media.expr_sum``), então aceita centenas de pontos.
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
    covered = media.expr_sum([f'gte(t,{s["points"][0][0]:.3f})*lt(t,{s["end"]:.3f})' for s in fill])
    terms.append(f'(1-min(1,{covered}))*0.5')
    return media.expr_sum(terms)


def fit_enable_expression(shots: list[dict], layout: str = 'fit') -> str | None:
    spans = [s for s in shots if s['layout'] == layout]
    if not spans:
        return None
    return media.expr_sum([f'between(t,{s["start"]:.3f},{s["end"]:.3f})' for s in spans])


def pip_enable_expression(shots: list[dict]) -> str | None:
    return fit_enable_expression(shots, 'pip')


# Janela dos streamers na transmissão (picture-in-picture): um vídeo 16:9 num canto, com moldura fixa.
PIP_MIN, PIP_MAX = 0.10, 0.45      # largura e altura, em fração do quadro
PIP_ASPECT = (1.5, 2.05)           # proporção em pixels (16:9 ≈ 1,78)
PIP_MOTION = 4.0                   # variação média (0–255) entre amostras dentro da janela: é vídeo, não arte fixa
PIP_BORDER = 0.6                   # fração da moldura visível numa cena para usar a janela nela


def _persistent_edges(samples: list[dict], need: float):
    import cv2
    import numpy as np
    kernel = np.ones((3, 3), np.uint8)
    return np.mean([cv2.dilate(cv2.Canny(s['gray'], 60, 160), kernel) > 0 for s in samples], axis=0) >= need


def detect_pip(samples: list[dict]) -> tuple[float, float, float, float] | None:
    """Janela com os streamers que a transmissão mostra num canto nas cenas fora da bancada, ou ``None``.

    Procura, nas amostras dessas cenas, um retângulo de moldura fixa numa lateral, em proporção de vídeo (16:9),
    com imagem em movimento dentro (placares e artes fixas ficam de fora).
    """
    import cv2
    import numpy as np
    if len(samples) < 8:
        return None
    picked = samples[::max(1, len(samples) // FACECAM_SAMPLES)]
    H, W = picked[0]['gray'].shape
    persist = _persistent_edges(picked, 0.8).astype(np.uint8) * 255
    contours, _ = cv2.findContours(persist, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        fw, fh, aspect = w / W, h / H, w / max(1, h)
        cx = (x + w / 2) / W
        if not (PIP_MIN <= fw <= PIP_MAX and PIP_MIN <= fh <= PIP_MAX and PIP_ASPECT[0] <= aspect <= PIP_ASPECT[1]):
            continue
        if FACECAM_SIDE_MAX < cx < 1 - FACECAM_SIDE_MAX:
            continue   # no meio da tela não é a janela lateral
        inner = [s['gray'][y + 3:y + h - 3, x + 3:x + w - 3].astype(np.float32) for s in picked]
        motion = float(np.mean([np.abs(a - b).mean() for a, b in zip(inner, inner[1:])]))
        if motion < PIP_MOTION:
            continue
        score = fw * fh - abs(aspect - 16 / 9) * 0.01
        if best is None or score > best[0]:
            best = (score, (x / W, y / H, fw, fh))
    return tuple(round(v, 4) for v in best[1]) if best else None


def pip_inner(rect, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Só o vídeo de dentro da janela: sem a moldura e em 16:9 a partir do topo (a faixa de texto da transmissão
    costuma ficar embaixo, dentro da mesma moldura)."""
    W, H = size
    x, y, w, h = rect[0] * W, rect[1] * H, rect[2] * W, rect[3] * H
    inset = max(2.0, w * 0.015)
    x, y, w, h = x + inset, y + inset, w - 2 * inset, h - 2 * inset
    h = min(h, w * 9 / 16)
    return tuple(round(v, 4) for v in (x / W, y / H, w / W, h / H))


def pip_visible(samples: list[dict], rect) -> bool:
    """A moldura da janela aparece nestas amostras (a cena tem a janela)?"""
    import numpy as np
    if not samples:
        return False
    H, W = samples[0]['gray'].shape
    x0, y0 = int(round(rect[0] * W)), int(round(rect[1] * H))
    x1 = min(W - 1, int(round((rect[0] + rect[2]) * W)) - 1)
    y1 = min(H - 1, int(round((rect[1] + rect[3]) * H)) - 1)
    edges = _persistent_edges(samples, 0.6)
    border = np.concatenate([edges[y0, x0:x1], edges[y1, x0:x1], edges[y0:y1, x0], edges[y0:y1, x1]])
    return float(border.mean()) >= PIP_BORDER
