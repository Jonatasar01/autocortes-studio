"""Importação opcional de vídeos públicos do YouTube e de lives gravadas da Twitch em processo cancelável.

Uma live da Twitch importada já fica com o link no projeto: os clipes do público entram na escolha dos cortes.
"""
import importlib.util
import json
import os
import signal
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from . import config, storage
from .errors import AppError, sanitize

TIMEOUT = 1800               # YouTube: 30 min
TWITCH_TIMEOUT = 8 * 3600    # uma live gravada tem horas e vários GB
TWITCH_RE = re.compile(r'/videos/(\d{5,15})/?')


def available():
    return importlib.util.find_spec('yt_dlp') is not None


def canonical_url(value):
    try:
        u = urlsplit(value.strip())
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
            raise ValueError()
        host = (u.hostname or '').lower()
        if host == 'youtu.be':
            vid = u.path.strip('/')
        elif host in {'twitch.tv', 'www.twitch.tv', 'm.twitch.tv'}:
            m = TWITCH_RE.fullmatch(u.path)
            if not m:
                raise ValueError()
            return 'https://www.twitch.tv/videos/' + m.group(1)
        elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            parts = u.path.strip('/').split('/')
            vid = parse_qs(u.query).get('v', [''])[0] if u.path == '/watch' else (
                parts[1] if len(parts) == 2 and parts[0] in {'shorts', 'embed'} else '')
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
            raise ValueError()
    except (ValueError, AttributeError):
        raise AppError('Use o link HTTPS de um vídeo público do YouTube ou Shorts, ou de uma live gravada da Twitch '
                       '(twitch.tv/videos/…), não de uma playlist, canal ou clipe.',
                       400, 'bad_video_url') from None
    return 'https://www.youtube.com/watch?v=' + vid


def download(url, folder, ctx):
    if not available():
        raise AppError('Importação por link não instalada. Instale o extra links com uv.', 400, 'download_missing')
    url = canonical_url(url)
    twitch = 'twitch.tv/videos/' in url
    fmt = 'b[height<=1080]/b' if twitch else 'bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]'
    args = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist', '--no-cache-dir',
            '--socket-timeout', '20', '--retries', '2', '--fragment-retries', '2', '--no-warnings',
            '--max-filesize', str(config.MAX_UPLOAD_BYTES),
            '--match-filters', f'!is_live & duration <= {config.MAX_SOURCE_SECONDS}',
            '--format', fmt,
            '--concurrent-fragments', '8' if twitch else '1',
            '--merge-output-format', 'mp4', '--write-info-json', '--no-progress',
            '--output', str(folder/'video.%(ext)s'), '--', url]
    if shutil.which('node'):
        args[3:3] = ['--js-runtimes', 'node']
    start = time.monotonic()
    with tempfile.TemporaryFile(mode='w+b') as log:
        process = subprocess.Popen(args, stdout=log, stderr=log,
                                   start_new_session=sys.platform != 'win32',
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        try:
            while process.poll() is None:
                ctx.check_cancel()
                if time.monotonic()-start > (TWITCH_TIMEOUT if twitch else TIMEOUT):
                    raise AppError('Download excedeu o tempo limite. Tente novamente ou envie o arquivo.', 504,
                                   'download_timeout')
                size = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
                if size > config.MAX_UPLOAD_BYTES*2 + 16*1024**2:
                    raise AppError('Download maior que o limite permitido.', 413, 'too_big')
                storage.ensure_free_space(0)
                ctx.progress(0.1, f'Baixando vídeo: {size/1024**2:.1f} MB recebidos')
                ctx.cancel_event.wait(0.3)
            if process.returncode:
                log.seek(0, 2)
                log.seek(max(0, log.tell()-4000))
                detail = log.read().decode('utf-8', 'replace')
                # Não devolve URLs assinadas, cookies ou caminhos do subprocesso.
                reason = 'O site recusou o download, o vídeo está indisponível ou requer login.'
                if 'ffmpeg' in detail.lower():
                    reason = 'Confira a instalação do FFmpeg.'
                raise AppError('Não foi possível importar o link. ' + reason, 422, 'download_failed')
        finally:
            if process.poll() is None:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    ctx.check_cancel()
    video = folder/'video.mp4'
    if not video.is_file() or video.stat().st_size > config.MAX_UPLOAD_BYTES:
        raise AppError('Nenhum vídeo compatível foi recebido dentro do limite.', 422, 'download_empty')
    info_file = folder/'video.info.json'
    info = json.loads(info_file.read_text(encoding='utf-8')) if info_file.is_file() else {}
    provenance = {'url': url, 'title': sanitize(info.get('title') or 'Vídeo importado', 110),
                  'uploader': sanitize(info.get('uploader') or '', 100), 'imported_at': time.time()}
    if twitch:
        provenance['twitch_vod'] = url.rsplit('/', 1)[1]
    return video, provenance


def run(ctx):
    from . import service
    twitch = 'twitch.tv/videos/' in str(ctx.params.get('url'))
    ctx.stage('download', 'Importando a live gravada da Twitch' if twitch else 'Importando vídeo público do YouTube')
    root = storage.data_dir()/'imports'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=ctx.id+'-', dir=root) as temp:
        video, provenance = download(ctx.params['url'], Path(temp), ctx)
        ctx.stage('validando', 'Validando o vídeo baixado e iniciando a análise')
        ctx.progress(0.85)
        with video.open('rb') as source:
            upload = service.Upload(provenance['title']+'.mp4', ctx.params['form'], video.stat().st_size,
                                    origin='twitch' if twitch else 'youtube')
            upload.automation_snapshot = ctx.params.get('automation_snapshot')
            upload.curation_snapshot = ctx.params.get('curation_snapshot')
            upload.provenance = provenance
            try:
                while chunk := source.read(4*1024**2):
                    ctx.check_cancel()
                    upload.write(chunk)
                result = upload.finish()
            except BaseException:
                upload.abort()
                raise
    return {'project_id': result['id'], 'name': provenance['title']}
