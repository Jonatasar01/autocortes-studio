"""Publicar um corte exportado no YouTube pela API oficial (YouTube Data API v3).

O usuário cria um cliente OAuth do tipo "App para computador" no Google Cloud (com a YouTube Data API ativada) e cola
o ID e a chave secreta. "Conectar" abre a página de login do próprio Google (com PKCE); o Google volta para
``/api/youtube/callback`` neste computador e o app guarda só o *refresh token* em ``data/secrets.json``. A senha da
conta nunca passa pelo app.

O envio é retomável (pedaços de 8 MB) e roda como tarefa na fila, com progresso e cancelamento. Projetos do Google
Cloud ainda não verificados pelo Google só podem publicar como **privado**: o vídeo sobe e o usuário muda a
visibilidade no YouTube Studio.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, secrets_store, storage
from .errors import AppError, Cancelled, sanitize

# Envio e leitura (as visualizações dos cortes publicados). Uma conta conectada antes só com o envio continua
# publicando; para ver os resultados, basta conectar de novo.
SCOPE = 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
REVOKE_URL = 'https://oauth2.googleapis.com/revoke'
UPLOAD_URL = 'https://www.googleapis.com/upload/youtube/v3/videos'
THUMB_URL = 'https://www.googleapis.com/upload/youtube/v3/thumbnails/set'
VIDEOS_URL = 'https://www.googleapis.com/youtube/v3/videos'
THUMB_MAX = 2 * 1024 * 1024
CHUNK = 8 * 1024 * 1024
PRIVACY = ('private', 'unlisted', 'public')
LOOPBACK = {'127.0.0.1', 'localhost', '[::1]', '::1'}
PENDING_TTL = 600

_lock = threading.Lock()
_pending: dict[str, dict] = {}     # state -> verificador PKCE e endereço de volta
_access: dict = {}


def credentials() -> tuple[str, str]:
    cid = secrets_store.get('youtube_id') or os.getenv('YOUTUBE_CLIENT_ID', '')
    secret = secrets_store.get('youtube_secret') or os.getenv('YOUTUBE_CLIENT_SECRET', '')
    return cid.strip(), secret.strip()


def status() -> dict:
    cid, secret = credentials()
    return {'configured': bool(cid and secret), 'connected': bool(secrets_store.get('youtube_refresh')),
            'key_hint': secrets_store.hint('youtube_id')}


def _call(req: urllib.request.Request, what: str) -> tuple[int, dict, dict]:
    """``(status, cabeçalhos, json)``; 308 (envio incompleto) é resposta normal, não erro."""
    try:
        with urllib.request.urlopen(req, timeout=config.CLOUD_TIMEOUT) as response:
            raw = response.read(4 * 1024 * 1024)
            code, headers = response.status, dict(response.headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 308:
            return 308, dict(exc.headers), {}
        body = exc.read(64 * 1024).decode('utf-8', 'replace')
        reason = ''
        try:
            err = json.loads(body).get('error')
            reason = (err.get('errors') or [{}])[0].get('reason', '') if isinstance(err, dict) else str(err or '')
        except (ValueError, AttributeError):
            pass
        messages = {'invalid_grant': 'A conexão com o YouTube expirou ou foi removida; conecte a conta de novo.',
                    'invalid_client': 'ID ou chave secreta do cliente OAuth inválidos.',
                    'quotaExceeded': 'Cota diária da API do YouTube esgotada; tente amanhã.',
                    'uploadLimitExceeded': 'O YouTube recusou: limite de envios da conta atingido por hoje.',
                    'youtubeSignupRequired': 'Esta conta Google ainda não tem um canal no YouTube.',
                    'forbidden': 'O YouTube recusou o envio (permissão negada).'}
        message = messages.get(reason) or (f'O YouTube recusou {what} ({exc.code}).' if exc.code < 500
                                           else 'YouTube indisponível no momento; tente de novo.')
        raise AppError(message, 502, 'youtube_http', {'technical': sanitize(reason, 80)}) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AppError('Não foi possível conectar ao YouTube.', 502, 'youtube_unreachable',
                       {'technical': sanitize(type(exc).__name__, 80)}) from None
    try:
        return code, headers, json.loads(raw.decode('utf-8')) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AppError('Resposta inválida do YouTube.', 502, 'youtube_bad_response') from None


# --------------------------------------------------------------------------- conexão da conta

def redirect_uri(host: str) -> str:
    """Endereço de volta do login: só neste computador (o Google aceita qualquer porta em 127.0.0.1/localhost)."""
    try:
        name = (urllib.parse.urlsplit('//' + host).hostname or '').lower()
    except ValueError:
        name = ''
    if name not in LOOPBACK:
        raise AppError('Conecte o YouTube pelo próprio computador do AutoCortes (http://127.0.0.1:8000): o Google '
                       'só devolve o login para este endereço.', 400, 'youtube_not_local')
    return f'http://{host}/api/youtube/callback'


def auth_url(host: str) -> str:
    cid, secret = credentials()
    if not (cid and secret):
        raise AppError('Cole o ID e a chave secreta do cliente OAuth antes de conectar.', 409, 'youtube_no_key')
    uri = redirect_uri(host)
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode()
    with _lock:
        now = time.time()
        for key in [k for k, v in _pending.items() if now - v['at'] > PENDING_TTL]:
            _pending.pop(key, None)
        _pending[state] = {'verifier': verifier, 'redirect': uri, 'at': now}
    return AUTH_URL + '?' + urllib.parse.urlencode({
        'client_id': cid, 'redirect_uri': uri, 'response_type': 'code', 'scope': SCOPE, 'state': state,
        'code_challenge': challenge, 'code_challenge_method': 'S256', 'access_type': 'offline',
        'prompt': 'consent'})


def finish(state: str, code: str) -> None:
    """Troca o código do login pelo refresh token e o guarda."""
    with _lock:
        pending = _pending.pop(state or '', None)
    if not pending or time.time() - pending['at'] > PENDING_TTL:
        raise AppError('Login do YouTube expirado ou de outra janela; clique em Conectar de novo.', 400,
                       'youtube_bad_state')
    cid, secret = credentials()
    body = urllib.parse.urlencode({'client_id': cid, 'client_secret': secret, 'code': code,
                                   'code_verifier': pending['verifier'], 'grant_type': 'authorization_code',
                                   'redirect_uri': pending['redirect']}).encode('ascii')
    _, _, data = _call(urllib.request.Request(TOKEN_URL, data=body, method='POST'), 'o login')
    refresh = data.get('refresh_token')
    if not isinstance(refresh, str):
        raise AppError('O Google não devolveu a permissão de envio; conecte de novo.', 502, 'youtube_bad_response')
    secrets_store.put('youtube_refresh', refresh)
    with _lock:
        _access.clear()
        if isinstance(data.get('access_token'), str):
            _access.update(value=data['access_token'], expires=time.time() + float(data.get('expires_in') or 3600))


def disconnect() -> None:
    refresh = secrets_store.get('youtube_refresh')
    if refresh:
        try:   # revoga no Google; sem internet, ao menos esquece aqui
            _call(urllib.request.Request(REVOKE_URL, data=urllib.parse.urlencode({'token': refresh}).encode(),
                                         method='POST'), 'a desconexão')
        except AppError:
            pass
    secrets_store.put('youtube_refresh', '')
    with _lock:
        _access.clear()


def access_token() -> str:
    with _lock:
        if _access.get('expires', 0) > time.time() + 60:
            return _access['value']
    refresh = secrets_store.get('youtube_refresh')
    if not refresh:
        raise AppError('Conecte a conta do YouTube em Configurações antes de publicar.', 409, 'youtube_not_connected')
    cid, secret = credentials()
    body = urllib.parse.urlencode({'client_id': cid, 'client_secret': secret, 'refresh_token': refresh,
                                   'grant_type': 'refresh_token'}).encode('ascii')
    _, _, data = _call(urllib.request.Request(TOKEN_URL, data=body, method='POST'), 'a renovação do acesso')
    if not isinstance(data.get('access_token'), str):
        raise AppError('Resposta inválida do YouTube.', 502, 'youtube_bad_response')
    with _lock:
        _access.update(value=data['access_token'], expires=time.time() + float(data.get('expires_in') or 3600))
        return _access['value']


# --------------------------------------------------------------------------- envio

def upload(path: Path, title: str, description: str, tags: list[str], privacy: str, *,
           publish_at: str | None = None, cancel_event: threading.Event | None = None, on_progress=None) -> str:
    """Envia o vídeo (retomável, pedaços de ``CHUNK``) e devolve o id dele no YouTube.

    ``publish_at`` (ISO 8601, UTC): o vídeo sobe privado e o YouTube publica sozinho nesse horário."""
    size = path.stat().st_size
    meta = {'snippet': {'title': title[:100], 'description': description[:4900], 'tags': tags[:15],
                        'categoryId': '24'},   # Entretenimento
            'status': {'privacyStatus': 'private' if publish_at else privacy, 'selfDeclaredMadeForKids': False}}
    if publish_at:
        meta['status']['publishAt'] = publish_at
    req = urllib.request.Request(UPLOAD_URL + '?uploadType=resumable&part=snippet,status',
                                 data=json.dumps(meta).encode('utf-8'), method='POST',
                                 headers={'Authorization': 'Bearer ' + access_token(),
                                          'Content-Type': 'application/json; charset=UTF-8',
                                          'X-Upload-Content-Type': 'video/mp4',
                                          'X-Upload-Content-Length': str(size)})
    _, headers, _ = _call(req, 'o início do envio')
    session = headers.get('Location') or headers.get('location')
    if not session or not session.startswith('https://www.googleapis.com/'):
        raise AppError('O YouTube não abriu o envio; tente de novo.', 502, 'youtube_bad_response')
    sent = 0
    with path.open('rb') as fh:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            fh.seek(sent)
            chunk = fh.read(CHUNK)
            last = sent + len(chunk) - 1
            put = urllib.request.Request(session, data=chunk, method='PUT', headers={
                'Authorization': 'Bearer ' + access_token(), 'Content-Length': str(len(chunk)),
                'Content-Range': f'bytes {sent}-{last}/{size}' if chunk else f'bytes */{size}'})
            code, headers, data = _call(put, 'o envio')
            if code in (200, 201):
                if on_progress:
                    on_progress(1.0)
                if not isinstance(data.get('id'), str):
                    raise AppError('Resposta inválida do YouTube.', 502, 'youtube_bad_response')
                return data['id']
            got = headers.get('Range') or headers.get('range') or ''   # "bytes=0-8388607": o que já chegou
            sent = int(got.rsplit('-', 1)[1]) + 1 if '-' in got else 0
            if on_progress:
                on_progress(sent / max(1, size))


def set_thumbnail(video_id: str, image: Path) -> None:
    """Troca a capa do vídeo no YouTube (o canal precisa ter a verificação por telefone)."""
    data = image.read_bytes()
    if len(data) > THUMB_MAX:
        raise AppError('Capa maior que 2 MB.', 400, 'bad_input')
    req = urllib.request.Request(THUMB_URL + '?videoId=' + urllib.parse.quote(video_id), data=data, method='POST',
                                 headers={'Authorization': 'Bearer ' + access_token(), 'Content-Type': 'image/jpeg'})
    _call(req, 'a capa')


def stats(video_ids: list[str]) -> dict[str, dict]:
    """Visualizações, curtidas e comentários de até 50 vídeos por pedido (1 unidade de cota cada)."""
    out: dict[str, dict] = {}
    ids = [v for v in dict.fromkeys(video_ids) if isinstance(v, str) and v]
    for k in range(0, len(ids), 50):
        url = VIDEOS_URL + '?' + urllib.parse.urlencode({'part': 'statistics,status', 'id': ','.join(ids[k:k + 50])})
        try:
            _, _, data = _call(urllib.request.Request(url, headers={'Authorization': 'Bearer ' + access_token()}),
                               'a leitura dos resultados')
        except AppError as exc:
            if exc.code == 'youtube_http' and 'insufficient' in str((exc.details or {}).get('technical', '')).lower():
                raise AppError('Para ver os resultados, conecte a conta do YouTube de novo em Configurações '
                               '(a permissão de leitura é nova).', 409, 'youtube_reconnect') from None
            raise
        for item in data.get('items') or []:
            st = item.get('statistics') or {}
            out[item.get('id')] = {'views': int(st.get('viewCount') or 0), 'likes': int(st.get('likeCount') or 0),
                                   'comments': int(st.get('commentCount') or 0),
                                   'privacy': (item.get('status') or {}).get('privacyStatus')}
    return out


def _publish_time(value: str | None) -> str | None:
    """Horário de publicação agendada em UTC (``2026-11-02T18:00:00Z``); precisa estar no futuro."""
    if not value:
        return None
    import datetime as dt
    try:
        when = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        raise AppError('Data de publicação inválida.', 400, 'bad_input') from None
    if when.tzinfo is None:
        raise AppError('Data de publicação sem fuso horário.', 400, 'bad_input')
    when = when.astimezone(dt.timezone.utc)
    if when < dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15):
        raise AppError('Agende para pelo menos 15 minutos a partir de agora.', 400, 'bad_input')
    return when.strftime('%Y-%m-%dT%H:%M:%SZ')


def request_publish(project_id: str, clip_id: int, fmt: str, title: str, description: str, privacy: str,
                    acknowledge_rights: bool = False, publish_at: str | None = None,
                    thumbnail: bool = False) -> dict:
    """Põe na fila o envio do corte exportado para o YouTube (``app/youtube.py``)."""
    if privacy not in PRIVACY:
        raise AppError('Visibilidade inválida.', 400, 'bad_input')
    if not status()['connected']:
        raise AppError('Conecte a conta do YouTube em Configurações antes de publicar.', 409, 'youtube_not_connected')
    from . import publish, service
    p = storage.read_project(project_id)
    clip = service._find_clip(p, clip_id)
    record = (clip.get('exports') or {}).get(fmt)
    if not record or not record.get('file'):
        raise AppError('Exporte o corte neste formato antes de publicar.', 409, 'not_exported')
    if (p.get('rights') or {}).get('requires_acknowledgement') and not acknowledge_rights:
        raise AppError('Há riscos relevantes de direitos neste projeto. Confirme que está ciente antes de publicar.',
                       409, 'rights_ack_required')
    title = service._clean_text(title, 100) or service._clean_text(record.get('title') or clip.get('title') or 'Corte',
                                                                    100)
    params = {'clip_id': clip_id, 'format': fmt, 'title': title, 'description': service._clean_text(description, 4900),
              'tags': publish.hashtags(clip.get('description') or '', fmt), 'privacy': privacy}
    params['tags'] = [t.lstrip('#') for t in params['tags']]
    params['publish_at'] = _publish_time(publish_at)
    if thumbnail:
        from . import thumbnails
        params['thumbnail'] = thumbnails.make(project_id, clip_id, fmt).name
    job, _ = service._jobs().submit(project_id, 'youtube', params, dedupe_key=f'youtube:{project_id}:{clip_id}:{fmt}')
    return service.public_job(job)


def run(ctx) -> dict:
    """Tarefa da fila: publica o corte exportado (``params``: clip_id, format, title, description, tags, privacy)."""
    prm = ctx.params
    p = storage.read_project(ctx.project_id)
    clip = next((c for c in p.get('clips') or [] if c['id'] == prm['clip_id']), None)
    record = ((clip or {}).get('exports') or {}).get(prm['format'])
    path = storage.project_dir(ctx.project_id) / 'clips' / (record or {}).get('file', '')
    if not record or not path.is_file() or path.parent.name != 'clips':
        raise AppError('Exporte o corte neste formato antes de publicar.', 409, 'not_exported')
    ctx.stage('youtube', 'Enviando o corte para o YouTube')
    video_id = upload(path, prm['title'], prm['description'], prm.get('tags') or [], prm['privacy'],
                      publish_at=prm.get('publish_at'), cancel_event=ctx.cancel_event,
                      on_progress=lambda f: ctx.progress(0.02 + 0.95 * f))
    result = {'id': video_id, 'url': 'https://youtu.be/' + video_id, 'privacy': prm['privacy'], 'at': time.time(),
              'title': prm['title'], 'publish_at': prm.get('publish_at')}
    thumb = path.parent / prm['thumbnail'] if prm.get('thumbnail') else None
    if thumb is not None and thumb.is_file() and thumb.parent == path.parent:
        ctx.stage('youtube', 'Enviando a capa')
        try:
            set_thumbnail(video_id, thumb)
            result['thumbnail'] = True
        except AppError as exc:   # o vídeo já subiu: a capa é um extra
            result['thumbnail_warning'] = ('O vídeo subiu, mas a capa não: ' + exc.message +
                                           ' (o canal precisa ter a verificação por telefone no YouTube).')
            ctx.warn(result['thumbnail_warning'])
    with storage.edit_project(ctx.project_id) as p:
        clip = next((c for c in p.get('clips') or [] if c['id'] == prm['clip_id']), None)
        rec = ((clip or {}).get('exports') or {}).get(prm['format'])
        if rec is not None:
            rec['youtube'] = result
    ctx.progress(1.0, 'Publicado no YouTube')
    return result
