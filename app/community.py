"""Clipes que o público fez na Twitch como referência para a escolha dos cortes.

Quem assiste a live marca os melhores momentos com o botão de clipe: cada clipe é um voto, e as visualizações dizem
quanto aquele momento rendeu. A API oficial (Helix) lista os clipes da live com ``vod_offset``, o ponto da live onde
o clipe começa. Esse ponto pode vir aproximado (ou nulo) e o vídeo do projeto pode ser só um pedaço da live, então,
com o yt-dlp instalado, o áudio de cada clipe é procurado no áudio do projeto (mesma energia RMS da camada de áudio,
correlação normalizada). Sem o yt-dlp, vale o ``vod_offset`` quando o vídeo do projeto é a live inteira.

Só o número do vídeo vai à Twitch; nada do projeto sai do computador. As credenciais (Client ID e Client Secret de
um app em dev.twitch.tv/console) ficam em ``data/secrets.json`` ou nas variáveis TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, secrets_store, selection
from .errors import AppError, Cancelled, sanitize

log = logging.getLogger('autocortes.community')

API = 'https://api.twitch.tv/helix'
TOKEN_URL = 'https://id.twitch.tv/oauth2/token'
VOD_RE = re.compile(r'^(?:https?://)?(?:www\.|m\.)?twitch\.tv/videos/(\d{5,15})(?:[/?#].*)?$', re.I)
MAX_PAGES = 10              # até 1000 clipes do canal no período da live
MAX_ALIGN = 40              # clipes mais vistos procurados pelo áudio (cada um é um download de poucos MB)
MIN_TEMPLATE = 5.0          # segundos de áudio do clipe para a busca valer
WINDOW = 150.0              # ± em torno do vod_offset quando o vídeo é a live inteira
NEAR_CORR = 0.55            # correlação mínima perto do ponto informado pela Twitch…
FAR_CORR = 0.7              # …e no vídeo inteiro, onde um falso encontro é mais provável
FAR_MARGIN = 0.08           # o melhor ponto precisa ganhar do segundo melhor (fora de ±10 s) por esta margem
SAME_MOMENT = 0.5           # clipes que se sobrepõem mais que isto são o mesmo momento
COVER = 0.6                 # um corte "cobre" o momento quando contém esta fração dele
LEAD, TAIL = 3.0, 2.0       # contexto antes e depois do momento num corte novo

_token_lock = threading.Lock()
_token: dict = {}


# --------------------------------------------------------------------------- credenciais

def credentials() -> tuple[str, str]:
    cid = secrets_store.get('twitch_id') or os.getenv('TWITCH_CLIENT_ID', '')
    secret = secrets_store.get('twitch_secret') or os.getenv('TWITCH_CLIENT_SECRET', '')
    return cid.strip(), secret.strip()


def aligner_available() -> bool:
    return importlib.util.find_spec('yt_dlp') is not None


def status() -> dict:
    cid, secret = credentials()
    saved = secrets_store.hint('twitch_id')
    return {'configured': bool(cid and secret), 'key_source': 'saved' if saved else ('env' if cid else None),
            'key_hint': saved, 'aligner': aligner_available()}


def parse_vod(value) -> str | None:
    """Número do vídeo a partir do link (twitch.tv/videos/123…) ou do número puro. Vazio: ``None``."""
    value = str(value or '').strip()
    if not value:
        return None
    if value.isdigit() and 5 <= len(value) <= 15:
        return value
    m = VOD_RE.match(value)
    if not m:
        raise AppError('Use o link de um vídeo da Twitch (twitch.tv/videos/…), não de um clipe ou canal.',
                       400, 'bad_twitch_url')
    return m.group(1)


# --------------------------------------------------------------------------- API da Twitch

def _call(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=config.CLOUD_TIMEOUT) as response:
            raw = response.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403) and TOKEN_URL in req.full_url:
            raise AppError('Client ID ou Client Secret da Twitch inválido.', 502, 'twitch_auth') from None
        if exc.code == 401:
            raise _Expired() from None
        message = {429: 'Limite de pedidos da Twitch atingido; tente de novo em um minuto.'}.get(exc.code) or (
            f'A Twitch recusou o pedido ({exc.code}).' if exc.code < 500 else 'Twitch indisponível no momento.')
        raise AppError(message, 502, 'twitch_http') from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AppError('Não foi possível conectar à Twitch.', 502, 'twitch_unreachable',
                       {'technical': sanitize(type(exc).__name__, 80)}) from None
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AppError('Resposta inválida da Twitch.', 502, 'twitch_bad_response') from None


class _Expired(Exception):
    pass


def _access_token(cid: str, secret: str, *, renew: bool = False) -> str:
    with _token_lock:
        if not renew and _token.get('cid') == cid and _token.get('expires', 0) > time.time() + 60:
            return _token['value']
        body = urllib.parse.urlencode({'client_id': cid, 'client_secret': secret,
                                       'grant_type': 'client_credentials'}).encode('ascii')
        data = _call(urllib.request.Request(TOKEN_URL, data=body, method='POST'))
        if not isinstance(data.get('access_token'), str):
            raise AppError('Resposta inválida da Twitch.', 502, 'twitch_bad_response')
        _token.update(cid=cid, value=data['access_token'], expires=time.time() + float(data.get('expires_in') or 3600))
        return _token['value']


def _helix(path: str, params: dict) -> dict:
    cid, secret = credentials()
    if not (cid and secret):
        raise AppError('Falta o Client ID e o Client Secret da Twitch em Escolha dos cortes.', 409, 'twitch_no_key')
    url = API + path + '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    for renew in (False, True):   # token vencido ou revogado: pede outro uma vez
        headers = {'Client-Id': cid, 'Authorization': 'Bearer ' + _access_token(cid, secret, renew=renew)}
        try:
            return _call(urllib.request.Request(url, headers=headers))
        except _Expired:
            continue
    raise AppError('Client ID ou Client Secret da Twitch inválido.', 502, 'twitch_auth')


def parse_duration(text: str) -> float:
    """Duração da Helix ("3h2m1s") em segundos."""
    m = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?', str(text or ''))
    if not m or not any(m.groups()):
        return 0.0
    h, mi, s = (int(g or 0) for g in m.groups())
    return float(h * 3600 + mi * 60 + s)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def fetch_clips(video_id: str) -> tuple[dict, list[dict]]:
    """A live (título, canal, duração) e os clipes feitos dela, do mais visto para o menos visto."""
    found = _helix('/videos', {'id': video_id}).get('data') or []
    if not found:
        raise AppError('Live não encontrada na Twitch (ela pode ter sido apagada ou ser só para inscritos).',
                       404, 'twitch_no_video')
    v = found[0]
    try:
        created = dt.datetime.fromisoformat(str(v['created_at']).replace('Z', '+00:00'))
    except (KeyError, ValueError):
        raise AppError('Resposta inválida da Twitch.', 502, 'twitch_bad_response') from None
    duration = parse_duration(v.get('duration'))
    video = {'id': video_id, 'title': sanitize(v.get('title') or '', 140), 'channel': sanitize(v.get('user_name') or '', 60),
             'duration': duration}
    # Os clipes são filtrados pela data em que foram feitos: durante a live e depois, enquanto o vídeo existe.
    ended = min(dt.datetime.now(dt.timezone.utc), created + dt.timedelta(seconds=duration, days=60))
    clips, cursor = [], None
    for _ in range(MAX_PAGES):
        page = _helix('/clips', {'broadcaster_id': v.get('user_id'), 'started_at': _iso(created),
                                 'ended_at': _iso(ended), 'first': 100, 'after': cursor})
        for c in page.get('data') or []:
            if str(c.get('video_id') or '') != video_id:
                continue
            offset = c.get('vod_offset')
            clips.append({'id': sanitize(c.get('id') or '', 120), 'title': sanitize(c.get('title') or '', 120),
                          'views': int(c.get('view_count') or 0), 'duration': float(c.get('duration') or 0),
                          'offset': float(offset) if isinstance(offset, (int, float)) else None,
                          'creator': sanitize(c.get('creator_name') or '', 60)})
        cursor = (page.get('pagination') or {}).get('cursor')
        if not cursor:
            break
    clips.sort(key=lambda c: (-c['views'], c['id']))
    return video, clips


# --------------------------------------------------------------------------- encaixe pelo áudio

def locate(template: list[float], track: list[float], hop: float, expected: float | None = None) -> tuple[float, float] | None:
    """Onde a energia do clipe (``template``) aparece na energia do vídeo (``track``): ``(segundos, correlação)``.

    Correlação de Pearson em cada posição (o volume do clipe pode ser outro: somar dB não muda nada). Com
    ``expected``, procura primeiro em ±``WINDOW`` dele; senão, no vídeo inteiro, com nota e margem maiores.
    """
    import numpy as np
    t = np.asarray(template, dtype=float)
    x = np.asarray(track, dtype=float)
    m = len(t)
    if m * hop < MIN_TEMPLATE or len(x) < m or t.std() < 1.0:
        return None   # clipe curto demais ou só silêncio: qualquer lugar "combina"
    t = (t - t.mean()) / (t.std() * m)
    num = np.correlate(x, t, mode='valid')
    cs = np.concatenate(([0.0], np.cumsum(x)))
    cs2 = np.concatenate(([0.0], np.cumsum(x * x)))
    mean = (cs[m:] - cs[:-m]) / m
    std = np.sqrt(np.maximum((cs2[m:] - cs2[:-m]) / m - mean ** 2, 0.0))
    corr = np.where(std > 0.5, num / np.maximum(std, 1e-9), 0.0)
    if expected is not None:
        lo = max(0, int((expected - WINDOW) / hop))
        hi = min(len(corr), int((expected + WINDOW) / hop) + 1)
        if lo < hi:
            k = lo + int(np.argmax(corr[lo:hi]))
            if corr[k] >= NEAR_CORR:
                return round(k * hop, 2), round(float(corr[k]), 3)
    k = int(np.argmax(corr))
    guard = int(10 / hop)
    rest = np.concatenate((corr[:max(0, k - guard)], corr[k + guard + 1:]))
    second = float(rest.max()) if len(rest) else -1.0
    if corr[k] >= FAR_CORR and corr[k] - second >= FAR_MARGIN:
        return round(k * hop, 2), round(float(corr[k]), 3)
    return None


def _clip_energy(clip_id: str, cancel_event: threading.Event | None) -> list[float] | None:
    """Baixa o clipe na menor qualidade (o áudio é o mesmo) e mede a energia como a camada de áudio mede o vídeo."""
    from . import audio_events, media
    work = Path(tempfile.mkdtemp(prefix='.clipe-twitch-', dir=config.DATA_DIR))
    try:
        url = 'https://clips.twitch.tv/' + urllib.parse.quote(clip_id, safe='-_')
        proc = subprocess.Popen([sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-cache-dir',
                                 '--quiet', '--no-warnings', '-f', 'worst[acodec!=none]/worst',
                                 '-o', str(work / 'clipe.%(ext)s'), '--', url],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=work)
        deadline = time.time() + 120
        while proc.poll() is None:
            if (cancel_event is not None and cancel_event.is_set()) or time.time() > deadline:
                proc.kill()
                proc.wait()
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                return None
            time.sleep(0.2)
        files = [f for f in work.iterdir() if f.is_file() and f.name.startswith('clipe.')]
        if proc.returncode != 0 or not files:
            return None
        info = media.probe_media(files[0])
        track = audio_events.energy_track(files[0], info, work / 'cache', cancel_event=cancel_event)
        return track['db'] if track else None
    except (media.FFmpegError, AppError, OSError) as exc:
        log.info('Clipe %s sem áudio para o encaixe: %s', clip_id, sanitize(exc, 200))
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def align(clips: list[dict], video: dict, duration: float, track: dict | None, *, previous: dict | None = None,
          cancel_event: threading.Event | None = None, on_progress=None) -> list[dict]:
    """Ponto de cada clipe no vídeo do projeto: ``start``/``end`` e ``method`` ("audio" ou "twitch").

    ``previous``: encaixes da busca anterior (por id do clipe), para não baixar de novo ao refazer a seleção.
    Clipes que não puderam ser encaixados voltam com ``start`` ``None``.
    """
    previous = previous or {}
    full = abs(duration - video['duration']) <= max(30.0, 0.01 * video['duration'])
    audio_ok = bool(track and track.get('db')) and aligner_available()
    out = []
    for n, clip in enumerate(clips):
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        item = dict(clip, start=None, end=None, method=None, match=None)
        old = previous.get(clip['id'])
        if old and old.get('start') is not None and old.get('method') in ('audio', 'twitch') and \
                (old['method'] == 'audio' or full):
            item.update(start=old['start'], end=old['end'], method=old['method'], match=old.get('match'))
        elif audio_ok and n < MAX_ALIGN:
            energy = _clip_energy(clip['id'], cancel_event)
            hit = locate(energy, track['db'], track['hop'], clip['offset'] if full else None) if energy else None
            if hit:
                item.update(start=hit[0], end=round(hit[0] + len(energy) * track['hop'], 2), method='audio',
                            match=hit[1])
        if item['start'] is None and full and clip['offset'] is not None:
            item.update(start=round(clip['offset'], 2), end=round(clip['offset'] + clip['duration'], 2), method='twitch')
        if item['start'] is not None:
            item['start'] = max(0.0, min(item['start'], duration))
            item['end'] = max(item['start'], min(item['end'], duration))
        out.append(item)
        if on_progress:
            on_progress((n + 1) / max(1, len(clips)))
    return out


def moments(aligned: list[dict]) -> list[dict]:
    """Clipes encaixados agrupados por momento (várias pessoas clipam o mesmo lance), do mais visto ao menos."""
    groups: list[dict] = []
    for c in sorted((c for c in aligned if c.get('start') is not None and c['end'] > c['start']),
                    key=lambda c: -c['views']):
        g = next((g for g in groups if selection._overlap(g, c) > SAME_MOMENT), None)
        if g is None:
            groups.append({'start': c['start'], 'end': c['end'], 'views': c['views'], 'count': 1,
                           'title': c['title'], 'clips': [c['id']], 'method': c['method']})
        else:
            g.update(start=min(g['start'], c['start']), end=max(g['end'], c['end']), views=g['views'] + c['views'],
                     count=g['count'] + 1, clips=g['clips'] + [c['id']])
            if c['method'] == 'audio':
                g['method'] = 'audio'
    groups.sort(key=lambda g: (-g['views'], g['start']))
    return groups


# --------------------------------------------------------------------------- seleção

def _views(n: int) -> str:
    return f'{n:,}'.replace(',', '.')


def _evidence(m: dict) -> tuple[dict, str]:
    if m.get('kind') == 'chat':
        detail = f"pico no chat: {m['messages']} mensagens, {str(m['ratio']).replace('.', ',')}× o normal"
        return {'tipo': 'pico no chat', 'inicio': m['start'], 'fim': m['end'], 'valor': m['messages'],
                'confianca': 'media', 'origem': 'chat', 'detalhe': detail}, detail
    detail = (f"{m['count']} clipe(s) do público na Twitch, {_views(m['views'])} visualizações"
              + ('' if m['method'] == 'audio' else ' (ponto informado pela Twitch)'))
    return {'tipo': 'clipe do público', 'inicio': m['start'], 'fim': m['end'], 'valor': m['views'],
            'confianca': 'alta' if m['method'] == 'audio' else 'media', 'origem': 'twitch', 'detalhe': detail}, detail


def apply(clips: list[dict], found: list[dict], segments: list[dict], duration: float,
          scenes: list[float] | None, target: int) -> list[dict]:
    """Põe os momentos clipados pelo público (e os picos do chat, ``kind`` "chat") na frente da lista de cortes.

    Um corte que já contém o momento ganha a origem ("publico" ou "chat") e a evidência; um momento que nenhum corte cobre
    vira um corte novo, do começo da frase em que o clipe começa até o fim da frase em que ele termina. Os
    demais cortes seguem depois, na ordem em que estavam; nenhum sai da lista.
    """
    units = selection.build_units(segments or [])
    backed, rest = [], [dict(c) for c in clips]
    for m in found:
        length = max(0.1, m['end'] - m['start'])
        evidence, detail = _evidence(m)
        chat = m.get('kind') == 'chat'
        field, source = ('chat_peak', 'chat') if chat else ('community', 'publico')
        info = {k: m[k] for k in (('messages', 'ratio') if chat else ('views', 'count', 'title', 'method'))}
        twin = next((c for c in backed + rest
                     if max(0.0, min(c['end'], m['end']) - max(c['start'], m['start'])) / length >= COVER), None)
        if twin is not None:
            if twin.get(field):
                continue   # dois momentos no mesmo corte: vale o mais forte, já aplicado
            twin.update({field: info}, sources=sorted(set(twin.get('sources') or []) | {source}),
                        evidence=(twin.get('evidence') or []) + [evidence],
                        reasons=[detail[:1].upper() + detail[1:]] + list(twin.get('reasons') or []))
            if twin in rest:
                rest.remove(twin)
                backed.append(twin)
            continue
        start, end = max(0.0, m['start'] - LEAD), min(duration, m['end'] + TAIL)
        if end - start < target:   # clipe curto: completa até a duração alvo, mais depois do que antes
            pad = target - (end - start)
            start, end = max(0.0, start - 0.3 * pad), min(duration, end + 0.7 * pad)
        inside = [u for u in units if u['end'] > start and u['start'] < end]
        if inside:   # sem cortar frase no meio
            start = max(0.0, min(start, inside[0]['start'] - 0.25))
            end = min(duration, max(end, inside[-1]['end'] + 0.4), start + config.MAX_CLIP_SECONDS)
        text = selection.collapse_loops(' '.join(u['text'] for u in inside))
        score = selection.score_clip(segments, start, end, scenes or []) if inside else None
        backed.append({'start': round(start, 2), 'end': round(end, 2), 'score': score, 'score_breakdown': None,
                       'confidence': evidence['confianca'], 'evidence': [evidence], 'warnings': [],
                       'sources': [source], 'reasons': [detail[:1].upper() + detail[1:]],
                       'selection_mode': source, field: info,
                       'title': (m.get('title') or selection._title_from_text(text)
                                 or ('Pico no chat' if chat else 'Momento clipado pelo público')),
                       'description': text[:600] + ('…' if len(text) > 600 else '')})
    merged = backed + rest
    for idx, clip in enumerate(merged, 1):
        clip.update(id=idx, rank=idx, export_file=None, exports={}, revision=1)
    return merged
