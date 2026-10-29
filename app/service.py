"""Camada de serviço: regras de negócio usadas pela API (independente do FastAPI, testável)."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Iterable

from . import (autopilot, config, curation, direction, downloads, filters, media, pipeline, publish, reframe, rights, settings,
               storage, subtitles, transcription, watcher)
from .errors import AppError
from .jobs import Handler, JobManager

log = logging.getLogger('autocortes.service')
manager: JobManager | None = None
_instance = None


# --------------------------------------------------------------------------- ciclo de vida

def startup() -> JobManager:
    global _instance, manager
    if manager is not None:
        return manager
    from .instance import InstanceLock
    _instance = InstanceLock(storage.data_dir())
    try:
        return _startup()
    except BaseException:
        if manager is not None:
            manager.stop()
            manager = None
        _instance.close()
        _instance = None
        raise


def _startup() -> JobManager:
    from . import advanced
    global manager
    if manager is not None:
        return manager
    removed = storage.cleanup_stale_files()
    if removed:
        log.info('Limpeza de %d temporário(s) da execução anterior.', removed)
    recovering = {item['id'] for item in storage.list_projects() if item.get('autopilot') == 'executando'}
    manager = JobManager(storage.data_dir() / 'autocortes.db', {
        'download': Handler(downloads.run, 1),
        'filmcraft': Handler(advanced.run, 1),
        'analysis': Handler(pipeline.run_analysis, config.ANALYSIS_WORKERS, _end_analysis),
        'export': Handler(pipeline.run_export, config.EXPORT_WORKERS, _end_export),
    })
    stale = manager.start()
    watcher.start()
    # Projetos "em análise" sem tarefa ativa (ex.: banco removido) ficam como interrompidos.
    for item in storage.list_projects():
        if item['id'] in recovering:
            with contextlib.suppress(AppError):
                autopilot.recover(item['id'])
        if item['status'] in ('queued', 'analyzing') and not manager.for_project(item['id'], active_only=True):
            with contextlib.suppress(AppError):
                with storage.edit_project(item['id']) as p:
                    p['status'] = 'interrupted'
                    p['error'] = 'A análise foi interrompida. Use "Analisar de novo" para tentar novamente.'
    # Análise cortada pelo fechamento do app (reinício, queda de energia): recomeça sozinha. Numa live longa,
    # esperar o usuário perceber e clicar custaria horas.
    for job in stale:
        if job['kind'] == 'analysis':
            with contextlib.suppress(AppError):
                _queue_analysis(job['project_id'], job.get('params') or {}, from_status=('interrupted',))
                log.info('Análise do projeto %s retomada após o reinício.', job['project_id'])
    return manager


def _end_analysis(ctx, status, error):
    pipeline.end_analysis(ctx, status, error)
    if status == 'done':
        try:
            autopilot.start(ctx.project_id)
        except AppError as exc:
            log.warning('Piloto automático não iniciou: %s', exc.message)


def _end_export(ctx, status, error):
    pipeline.end_export(ctx, status, error)
    autopilot.after_job('export', ctx, status, error)


def shutdown() -> None:
    global manager, _instance
    watcher.stop()
    if manager is not None:
        manager.stop()
        manager = None
    if _instance is not None:
        _instance.close()
        _instance = None


def _jobs() -> JobManager:
    if manager is None:
        raise AppError('Serviço de processamento não iniciado.', 503, 'not_started')
    return manager


# --------------------------------------------------------------------------- validação de entrada

def _choice(value: str, allowed: set[str], message: str) -> str:
    value = (value or '').strip()
    if value not in allowed:
        raise AppError(message, 400, 'bad_input')
    return value


def _int_range(value, low: int, high: int, message: str) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise AppError(message, 400, 'bad_input') from None
    if not low <= number <= high:
        raise AppError(message, 400, 'bad_input')
    return number


def _clean_text(value: str, limit: int) -> str:
    value = unicodedata.normalize('NFC', str(value or ''))
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    return value.strip()[:limit]


def validate_options(form: dict) -> dict:
    truthy = {'1', 'true', 'on', 'yes', 'sim'}
    return {
        'source_rights': _choice(form.get('source_rights', 'unknown'), rights.SOURCE_RIGHTS, 'Origem de direitos inválida.'),
        'external_audio': _choice(form.get('external_audio', 'unknown'), rights.YES_NO_UNKNOWN, 'Resposta de áudio inválida.'),
        'graphics_rights': _choice(form.get('graphics_rights', 'unknown'), rights.GRAPHICS, 'Resposta sobre imagens inválida.'),
        'third_party_footage': _choice(form.get('third_party_footage', 'unknown'), rights.YES_NO_UNKNOWN,
                                       'Resposta sobre trechos de terceiros inválida.'),
        'monetization': _choice(form.get('monetization', 'unknown'), rights.YES_NO_UNKNOWN, 'Resposta sobre monetização inválida.'),
        'license_notes': _clean_text(form.get('license_notes', ''), 2000),
        'target_seconds': _int_range(form.get('target_seconds', 35), 10, 90, 'Duração por corte deve ficar entre 10 e 90 s.'),
        # 0 = automático: entram os cortes com nota boa, sem número fixo (limite de segurança por hora de vídeo).
        'desired_count': _int_range(form.get('desired_count', 0), 0, 15,
                                    'Quantidade de cortes deve ficar entre 1 e 15 (ou 0 = automático).'),
        # Piloto automático: ausente = padrão das Configurações
        'autopilot': (None if str(form.get('autopilot', '')).strip() == '' else
                      str(form.get('autopilot')).strip().lower() in truthy),
    }


def display_name(filename: str | None) -> str:
    name = Path(_clean_text(filename or '', 300).replace('\\', '/')).name
    return name[:120] or 'video'


# --------------------------------------------------------------------------- criação (upload)

class Upload:
    """Upload em fluxo direto para a pasta do projeto, com limite real e verificação de espaço."""

    def __init__(self, filename: str | None, form: dict, declared_size: int | None = None,
                 origin: str = 'upload', fingerprint: str | None = None):
        self.name = display_name(filename)
        self.extension = Path(self.name).suffix.lower()
        if self.extension not in media.ALLOWED_EXTENSIONS:
            raise AppError('Selecione um vídeo válido: MP4, MOV, MKV, WEBM, AVI ou M4V.', 400, 'bad_extension')
        self.editing_mode = str(form.get('editing_mode', 'automatic')).strip().lower()
        if self.editing_mode not in ('manual', 'automatic'):
            raise AppError('Modo de edição inválido.', 400, 'bad_editing_mode')
        self.options = validate_options(form)
        self.automation_snapshot = (None if self.editing_mode == 'manual'
                                    else autopilot.snapshot(self.options['autopilot']))
        self.curation_snapshot = copy.deepcopy(settings.load()['curation'])
        self.provenance = None
        self.origin = origin
        self.fingerprint = fingerprint
        if declared_size is not None and declared_size > config.MAX_UPLOAD_BYTES:
            raise AppError(f'Vídeo maior que o limite de {config.MAX_UPLOAD_BYTES / 1024 ** 3:.0f} GB.', 413, 'too_big')
        storage.ensure_free_space(declared_size or 0)
        self.project_id, self.folder = storage.new_project_dir()
        self.part = self.folder / f'upload{self.extension}.part'
        self.handle = self.part.open('wb')
        self.size = 0
        self._next_space_check = 256 * 1024 ** 2

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > config.MAX_UPLOAD_BYTES:
            raise AppError(f'Vídeo maior que o limite de {config.MAX_UPLOAD_BYTES / 1024 ** 3:.0f} GB.', 413, 'too_big')
        if self.size >= self._next_space_check:
            self._next_space_check += 256 * 1024 ** 2
            storage.ensure_free_space(0, self.folder)
        self.handle.write(chunk)

    def abort(self) -> None:
        with contextlib.suppress(OSError):
            self.handle.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def finish(self) -> dict:
        try:
            self.handle.close()
            if self.size == 0:
                raise AppError('Arquivo vazio.', 400, 'empty')
            info = media.probe_media(self.part)          # valida o conteúdo real, não só a extensão
            warnings = media.integrity_warnings(self.part, info)
            stored = 'source' + self.extension
            os.replace(self.part, self.folder / stored)
            options = dict(self.options)
            options.pop('autopilot')
            project = {'id': self.project_id, 'name': self.name, 'filename': stored, 'status': 'queued',
                       'created_at': time.time(), 'media': info, 'warnings': warnings, 'origin': self.origin,
                       'source_fingerprint': self.fingerprint,
                       'clips': [], 'transcript': [], **options,
                       'autopilot': self.automation_snapshot, 'curation_profile': self.curation_snapshot,
                       'provenance': self.provenance}
            if self.editing_mode == 'manual':
                project.update({
                    'status': 'ready', 'editing_mode': 'manual', 'ready_at': time.time(),
                    'selection': {'mode': 'manual', 'reason': 'Os cortes serão definidos pelo usuário.'},
                    'transcription': {'state': 'not_requested', 'message': 'Transcrição não solicitada.'},
                    'transcript_status': 'Transcrição não solicitada.',
                    'rights': rights.rights_review(
                        options['source_rights'], options['external_audio'], options['license_notes'],
                        info['audio'], options['graphics_rights'], options['third_party_footage'],
                        options['monetization']),
                })
            storage.write_project(self.project_id, project)
            if self.editing_mode == 'manual':
                return {'id': self.project_id, 'status': 'ready', 'warnings': warnings}
            job, _ = _jobs().submit(self.project_id, 'analysis', {}, dedupe_key=f'analysis:{self.project_id}')
            with storage.edit_project(self.project_id) as p:
                p['analysis_job'] = job['id']
            return {'id': self.project_id, 'status': 'queued', 'job': public_job(job), 'warnings': warnings}
        except BaseException:
            self.abort()
            raise


def create_project(chunks: Iterable[bytes], filename: str, form: dict, declared_size: int | None = None,
                   origin: str = 'upload', fingerprint: str | None = None) -> dict:
    upload = Upload(filename, form, declared_size, origin, fingerprint)
    try:
        for chunk in chunks:
            upload.write(chunk)
    except BaseException:
        upload.abort()
        raise
    return upload.finish()


def _local_video(path_text: str) -> Path:
    """Caminho de vídeo digitado pelo usuário (app local): absoluto, existente e com extensão de vídeo."""
    text = str(path_text or '').strip().strip('"').strip("'")
    # Refuse network/device namespaces and alternate data streams before any filesystem access.
    normalized = text.replace('\\', '/')
    if normalized.startswith('//') or ':' in normalized[2:]:
        raise AppError('Use um arquivo de vídeo em um disco local, sem caminho de rede ou dispositivo.',
                       400, 'bad_path')
    source = Path(text).expanduser()
    if not text or not source.is_absolute():
        raise AppError('Informe o caminho completo do vídeo (ex.: D:\\lives\\live.mp4).', 400, 'bad_path')
    if source.suffix.lower() not in media.ALLOWED_EXTENSIONS:
        raise AppError('Selecione um vídeo válido: MP4, MOV, MKV, WEBM, AVI ou M4V.', 400, 'bad_extension')
    if not source.is_file():
        raise AppError('Arquivo não encontrado nesse caminho.', 404, 'not_found')
    try:
        source = source.resolve(strict=True)
    except (OSError, RuntimeError):
        raise AppError('Não foi possível resolver o caminho do vídeo.', 400, 'bad_path') from None
    if str(source).replace('\\', '/').startswith('//') or source.suffix.lower() not in media.ALLOWED_EXTENSIONS:
        raise AppError('O destino precisa ser um vídeo em disco local.', 400, 'bad_path')
    # Extension alone is insufficient: do not fingerprint arbitrary files disguised as videos.
    media.probe_media(source)
    return source


def check_path(path_text: str) -> dict:
    """O que acontece ao importar este arquivo do disco: tamanho, se vai copiar e se já está num projeto."""
    source = _local_video(path_text)
    size = source.stat().st_size
    same_disk = source.stat().st_dev == storage.data_dir().stat().st_dev
    return {'name': source.name, 'size_bytes': size, 'no_copy': same_disk,
            'too_big': size > config.MAX_UPLOAD_BYTES,
            'duplicate': find_duplicate(size, quick_fingerprint(source))}


def create_from_path(path_text: str, form: dict) -> dict:
    """Projeto a partir de um vídeo que já está no computador, sem enviar pelo navegador.

    No mesmo disco da pasta de dados vira um *hard link*: instantâneo e sem ocupar espaço (o projeto continua com o
    vídeo mesmo que o original seja apagado ou movido). Em outro disco, cópia local.
    """
    source = _local_video(path_text)
    size = source.stat().st_size
    upload = Upload(source.name, form, size, origin='disco')
    try:
        upload.handle.close()
        upload.part.unlink(missing_ok=True)
        try:
            os.link(source, upload.part)
            how = 'link'
        except OSError:        # outro disco (ou sistema de arquivos sem hard link): cópia
            shutil.copyfile(source, upload.part)
            how = 'copia'
        upload.size = size
    except BaseException:
        upload.abort()
        raise
    result = upload.finish()
    with storage.edit_project(result['id']) as p:
        p['source_link'] = how
        p['quick_fingerprint'] = quick_fingerprint(storage.project_dir(result['id']) / p['filename'])
    return result | {'no_copy': how == 'link'}


# --------------------------------------------------------------------------- consultas

def public_job(job: dict | None) -> dict | None:
    if not job:
        return None
    return {k: job.get(k) for k in ('id', 'kind', 'status', 'stage', 'progress', 'message', 'error',
                                    'created', 'started', 'finished')} | {
        'clip_id': job['params'].get('clip_id'), 'format': job['params'].get('format'),
        'preview': bool(job['params'].get('preview')),
        'result': job.get('result')}


def project_jobs(project_id: str) -> list[dict]:
    """Exportações, prévias e narrações do projeto para o painel de exportações. Não lê o project.json:
    o painel consulta isto a cada 1,5 s enquanto houver algo na fila."""
    storage.project_dir(project_id)
    return [public_job(j) for j in _jobs().for_project(project_id, limit=100) if j['kind'] != 'analysis']


def get_project(project_id: str, *, include_transcript: bool = False) -> dict:
    """Projeto para a interface. A transcrição fica de fora por padrão: numa live longa ela passa de 3 MB e
    o editor não a usa (a tela recarregava tudo isso a cada edição)."""
    p = storage.read_project(project_id)
    p.pop('filename', None)
    segments = p.get('transcript') or []
    p['transcript_segments'] = len(segments)
    if not include_transcript:
        p.pop('transcript', None)
    jobs = _jobs().for_project(project_id, limit=60)
    p['jobs'] = [public_job(j) for j in jobs]
    analysis = next((j for j in jobs if j['id'] == p.get('analysis_job')), None)
    p['analysis'] = public_job(analysis)
    if analysis:
        p['analysis_events'] = _jobs().events(analysis['id'], 20)
    return p


def list_projects() -> list[dict]:
    """Projetos recentes; os que estão em análise trazem a etapa e o andamento para a lista mostrar."""
    items = storage.list_projects()
    for item in items:
        if item.get('status') in ('queued', 'analyzing'):
            job = next((j for j in _jobs().for_project(item['id'], active_only=True, limit=5)
                        if j['kind'] == 'analysis'), None)
            if job:
                item['analysis'] = {k: job.get(k) for k in ('status', 'stage', 'progress', 'message', 'started')}
    return items


def get_job(job_id: str) -> dict:
    if not storage.valid_id(job_id):
        raise AppError('Tarefa não encontrada.', 404, 'not_found')
    job = _jobs().get(job_id)
    if not job:
        raise AppError('Tarefa não encontrada.', 404, 'not_found')
    return public_job(job) | {'project_id': job['project_id'], 'events': _jobs().events(job_id, 30)}


def cancel_job(job_id: str) -> dict:
    get_job(job_id)
    return public_job(_jobs().cancel(job_id))


# --------------------------------------------------------------------------- edição e exportação

def _find_clip(p: dict, clip_id: int) -> dict:
    clip = next((c for c in p.get('clips', []) if c['id'] == clip_id), None)
    if not clip:
        raise AppError('Trecho não encontrado.', 404, 'not_found')
    return clip


def _require_ready(p: dict) -> None:
    if p.get('status') != 'ready':
        raise AppError('A análise ainda não foi concluída.', 409, 'not_ready')


FEEDBACK = ('bom', 'rejeitado')


def set_feedback(project_id: str, clip_id: int, verdict: str | None, reason: str = '') -> dict:
    """Marca o corte como bom ou rejeitado (``None`` limpa).

    Fica no corte (``feedback``) e num registro do projeto (``feedback_log``) com o trecho e o título, que sobrevive a
    "Refazer seleção" e "Analisar de novo": é a referência para medir o motor (``scripts/avaliar_selecao.py``).
    Só local; nada é enviado para fora.
    """
    if verdict not in (None, *FEEDBACK):
        raise AppError('Use "bom", "rejeitado" ou vazio.', 400, 'bad_input')
    with storage.edit_project(project_id) as p:
        clip = _find_clip(p, clip_id)
        log_entries = [e for e in p.get('feedback_log') or []
                       if not (abs(e['start'] - clip['start']) < 0.5 and abs(e['end'] - clip['end']) < 0.5)]
        if verdict is None:
            clip.pop('feedback', None)
        else:
            entry = {'verdict': verdict, 'reason': _clean_text(reason, 200), 'at': time.time()}
            clip['feedback'] = entry
            log_entries.append({**entry, 'start': clip['start'], 'end': clip['end'], 'title': clip.get('title'),
                                'score': clip.get('score'), 'sources': clip.get('sources')})
        p['feedback_log'] = log_entries
        return {'clip_id': clip_id, 'feedback': clip.get('feedback'),
                'counts': {v: sum(1 for e in log_entries if e['verdict'] == v) for v in FEEDBACK}}


def edit_clip(project_id: str, clip_id: int, start: float, end: float, title: str) -> dict:
    title = _clean_text(title, 120)
    if not title:
        raise AppError('Informe um título.', 400, 'bad_input')
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)):
        raise AppError('Tempos inválidos.', 400, 'bad_input')
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        duration = p['media']['duration']
        if start < 0 or end <= start + 0.99 or end > duration + 0.02:
            raise AppError('Informe início e fim válidos dentro da duração do vídeo (mínimo de 1 s).', 400, 'bad_range')
        if end - start > config.MAX_CLIP_SECONDS:
            raise AppError(f'Cada corte pode ter no máximo {config.MAX_CLIP_SECONDS} s.', 400, 'bad_range')
        clip = _find_clip(p, clip_id)
        changed = (round(start, 3), round(min(end, duration), 3)) != (clip['start'], clip['end'])
        clip.update({'start': round(start, 3), 'end': round(min(end, duration), 3), 'title': title})
        if changed:
            clip['revision'] = int(clip.get('revision') or 1) + 1
            clip['edited_manually'] = True
        return dict(clip)


# --------------------------------------------------------------------------- área do streamer definida à mão

FOCUS_MIN = 0.05   # lado mínimo da área (fração do quadro)


def set_focus_rect(project_id: str, clip_id: int, rect: list | None) -> dict:
    """Área do quadro (``x, y, largura, altura`` em fração do vídeo inteiro) que o "Só o streamer" mostra neste corte,
    no lugar da janela achada sozinha. ``None`` volta para a detecção automática."""
    if rect is not None:
        if (not isinstance(rect, (list, tuple)) or len(rect) != 4
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in rect)):
            raise AppError('Área inválida.', 400, 'bad_input')
        x, y, w, h = (float(v) for v in rect)
        x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
        w, h = min(w, 1.0 - x), min(h, 1.0 - y)
        if w < FOCUS_MIN or h < FOCUS_MIN:
            raise AppError('Desenhe uma área maior (pelo menos 5% do vídeo em cada lado).', 400, 'bad_input')
        rect = [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]
    with storage.edit_project(project_id) as p:
        clip = _find_clip(p, clip_id)
        if rect is None:
            clip.pop('focus_rect', None)
        else:
            clip['focus_rect'] = rect
        return {'clip_id': clip_id, 'focus_rect': clip.get('focus_rect')}


# --------------------------------------------------------------------------- texto da legenda

def _in_clip(seg: dict, clip: dict) -> bool:
    return clip['start'] <= (seg['start'] + seg['end']) / 2 <= clip['end']


def clip_subtitles(project_id: str, clip_id: int) -> dict:
    """Frases da transcrição dentro do corte, na ordem: o texto que vira a legenda."""
    p = storage.read_project(project_id)
    clip = _find_clip(p, clip_id)
    segs = [s for s in p.get('transcript') or [] if _in_clip(s, clip)]
    refined = pipeline._refined(p, clip['start'], clip['end'])
    return {'clip_id': clip_id, 'refined': refined,
            'segments': [{'start': s['start'], 'end': s['end'], 'text': s.get('text', ''),
                          'edited': bool(s.get('edited'))} for s in segs]}


def _retime_words(seg: dict, text: str) -> list[dict]:
    """Palavras do texto corrigido com tempo: as originais quando a quantidade bate; senão, a duração da frase
    dividida pelo tamanho de cada palavra (a legenda animada destaca uma por vez)."""
    words = text.split()
    old = seg.get('words') or []
    if len(old) == len(words):
        return [{**w, 'word': new} for w, new in zip(old, words)]
    start, end = seg['start'], seg['end']
    total = sum(len(w) + 1 for w in words) or 1
    out, t = [], start
    for w in words:
        dt = (end - start) * (len(w) + 1) / total
        out.append({'start': round(t, 2), 'end': round(t + dt, 2), 'word': w})
        t += dt
    return out


def set_clip_subtitles(project_id: str, clip_id: int, segments: list[dict]) -> dict:
    """Corrige o texto das frases do corte (identificadas por início e fim). Texto vazio apaga a frase.
    As frases corrigidas ficam marcadas e a "legenda precisa" não as substitui depois."""
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        clip = _find_clip(p, clip_id)
        # Cópia: a transcrição lida fica em cache, e o storage só regrava quando recebe uma lista nova.
        transcript = copy.deepcopy(p.get('transcript') or [])
        changed = 0
        for item in segments:
            text = _clean_text(str(item.get('text') or ''), 400)
            seg = next((s for s in transcript if _in_clip(s, clip) and abs(s['start'] - float(item['start'])) < 0.01
                        and abs(s['end'] - float(item['end'])) < 0.01), None)
            if seg is None:
                raise AppError('A legenda mudou enquanto você editava. Abra o corte de novo.', 409, 'stale')
            if text == seg.get('text', '').strip():
                continue
            changed += 1
            if not text:
                transcript.remove(seg)
                continue
            seg.update({'text': text, 'words': _retime_words(seg, text), 'edited': True})
        p['transcript'] = transcript
    return clip_subtitles(project_id, clip_id) | {'changed': changed}



# --------------------------------------------------------------------------- enquadramento por trecho

FRAMING_MIN_SEGMENT = 0.5    # trechos menores que isso são absorvidos pelo vizinho
AUTO_FRAMING_CYCLE = ('track', 'blur')   # variação automática: seguir rosto, depois fundo desfocado…
AUTO_FRAMING_MIN = 2.5       # trecho automático mínimo (cenas mais curtas se juntam à seguinte)
AUTO_FRAMING_EVERY = 6.0     # sem trocas de cena no corte, alterna a cada ~6 s


def clean_framing_segments(raw: list | None, clip_len: float) -> list[dict]:
    """Trechos ``{start, end, framing}`` (em segundos do corte) ordenados, sem buracos e sem sobreposição.

    Cobrem o corte inteiro: o primeiro começa em 0 e o último termina no fim; trechos vizinhos com o mesmo
    enquadramento são unidos. Trechos fora do corte (depois de encurtá-lo) são cortados.
    """
    items = []
    for item in raw or []:
        if not isinstance(item, dict):
            raise AppError('Trecho de enquadramento inválido.', 400, 'bad_input')
        framing = item.get('framing')
        if framing not in media.SEGMENT_FRAMINGS:
            raise AppError('Enquadramento do trecho inválido (use blur, fit, track, streamer ou split).', 400, 'bad_framing')
        try:
            a, b = float(item['start']), float(item['end'])
        except (KeyError, TypeError, ValueError):
            raise AppError('Tempos do trecho inválidos.', 400, 'bad_input') from None
        if not (math.isfinite(a) and math.isfinite(b)):
            raise AppError('Tempos do trecho inválidos.', 400, 'bad_input')
        a, b = max(0.0, a), min(clip_len, b)
        if b - a >= FRAMING_MIN_SEGMENT:
            items.append({'start': a, 'end': b, 'framing': framing})
    items.sort(key=lambda s: s['start'])
    out: list[dict] = []
    for seg in items:
        if out and seg['start'] < out[-1]['end']:
            seg['start'] = out[-1]['end']          # sobreposição: vale o trecho que começou antes
            if seg['end'] - seg['start'] < FRAMING_MIN_SEGMENT:
                continue
        if out:
            out[-1]['end'] = seg['start']          # buraco: o trecho anterior vai até o próximo
        if out and out[-1]['framing'] == seg['framing']:
            out[-1]['end'] = seg['end']
        else:
            out.append(seg)
    if out:
        out[0]['start'] = 0.0
        out[-1]['end'] = clip_len
    return [{'start': round(s['start'], 3), 'end': round(s['end'], 3), 'framing': s['framing']} for s in out]


def auto_framing_segments(p: dict, clip: dict) -> list[dict]:
    """Variação automática: alterna seguir rosto e fundo desfocado a cada troca de cena do corte."""
    clip_len = clip['end'] - clip['start']
    cuts = sorted(s - clip['start'] for s in (p.get('scenes') or []) if clip['start'] < s < clip['end'])
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= AUTO_FRAMING_MIN and clip_len - c >= AUTO_FRAMING_MIN:
            bounds.append(c)
    if len(bounds) == 1:   # nenhuma troca de cena útil: alterna em intervalos regulares
        n = max(1, round(clip_len / AUTO_FRAMING_EVERY))
        bounds = [clip_len * k / n for k in range(n)]
    bounds.append(clip_len)
    raw = [{'start': a, 'end': b, 'framing': AUTO_FRAMING_CYCLE[k % len(AUTO_FRAMING_CYCLE)]}
           for k, (a, b) in enumerate(zip(bounds, bounds[1:]))]
    return clean_framing_segments(raw, clip_len)


def set_framing_segments(project_id: str, clip_id: int, segments: list | None, *, auto: bool = False) -> dict:
    """Grava os trechos de enquadramento do corte (``auto``: gera a variação automática)."""
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        clip = _find_clip(p, clip_id)
        clip_len = clip['end'] - clip['start']
        clip['framing_segments'] = (auto_framing_segments(p, clip) if auto
                                    else clean_framing_segments(segments, clip_len))
        return {'clip_id': clip_id, 'segments': clip['framing_segments']}


def create_manual_clip(project_id: str, start: float, end: float, title: str) -> dict:
    """Cria um corte a partir da seleção do usuário, sem depender de IA ou transcrição."""
    title = _clean_text(title, 120)
    if not title:
        raise AppError('Informe um título.', 400, 'bad_input')
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)):
        raise AppError('Tempos inválidos.', 400, 'bad_input')
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        duration = p['media']['duration']
        if start < 0 or end <= start + 0.99 or end > duration + 0.02 or end - start > config.MAX_CLIP_SECONDS:
            raise AppError('Seleção fora do vídeo (mínimo 1 s, máximo permitido por corte).', 400, 'bad_range')
        clips = p.setdefault('clips', [])
        next_id = max((int(c['id']) for c in clips), default=0) + 1
        next_id = max(next_id, int(p.get('next_manual_clip_id') or 1))
        p['next_manual_clip_id'] = next_id + 1
        clip = {'id': next_id, 'start': round(start, 3), 'end': round(min(end, duration), 3),
                'title': title, 'description': 'Corte criado manualmente.',
                'score': None, 'selection_mode': 'manual', 'edited_manually': True,
                'revision': 1, 'rank': len(clips) + 1, 'exports': [], 'reasons': []}
        clip['exports'] = {}
        clips.append(clip)
        return dict(clip)


def delete_manual_clip(project_id: str, clip_id: int) -> dict:
    """Exclui a seleção, preservando arquivos já exportados como histórico no disco."""
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        _find_clip(p, clip_id)
        if manager is not None:
            active = manager.for_project(project_id, active_only=True)
            if any(j.get('params', {}).get('clip_id') == clip_id for j in active):
                raise AppError('Há uma tarefa usando este corte. Aguarde ou cancele a tarefa.', 409, 'clip_busy')
        p['clips'] = [c for c in p['clips'] if c['id'] != clip_id]
        for i, clip in enumerate(p['clips'], 1):
            clip['rank'] = i
        return {'deleted': clip_id}


def split_manual_clip(project_id: str, clip_id: int, at: float) -> dict:
    """Divide um corte em dois, atomicamente, sem abandonar edições anteriores."""
    if not math.isfinite(at):
        raise AppError('Posição inválida.', 400, 'bad_input')
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        if manager is not None and any(j.get('params', {}).get('clip_id') == clip_id
                                       for j in manager.for_project(project_id, active_only=True)):
            raise AppError('Há uma tarefa usando este corte.', 409, 'clip_busy')
        original = _find_clip(p, clip_id)
        if at - original['start'] < 1 or original['end'] - at < 1:
            raise AppError('A divisão exige ao menos 1 segundo de cada lado.', 400, 'bad_range')
        next_id = max(max((int(c['id']) for c in p['clips']), default=0) + 1,
                      int(p.get('next_manual_clip_id') or 1))
        p['next_manual_clip_id'] = next_id + 1
        new_clip = {'id': next_id, 'start': round(at, 3), 'end': original['end'],
                    'title': original['title'] + ' — parte 2', 'description': 'Dividido manualmente.',
                    'score': None, 'selection_mode': 'manual', 'edited_manually': True,
                    'revision': 1, 'exports': {}, 'reasons': []}
        original['end'] = round(at, 3)
        original['revision'] = int(original.get('revision') or 1) + 1
        original['edited_manually'] = True
        original['score'] = None
        original['selection_mode'] = 'manual'
        index = p['clips'].index(original)
        p['clips'].insert(index + 1, new_clip)
        for i, clip in enumerate(p['clips'], 1):
            clip['rank'] = i
        return {'first': dict(original), 'second': dict(new_clip)}


# --------------------------------------------------------------------------- plano de edição

def _plan_cache(project_id: str, clip: dict) -> Path:
    return storage.project_dir(project_id) / 'direction' / f"clip_{int(clip['id']):02d}.json"


def _analysis_for(project_id: str, p: dict, clip: dict) -> dict:
    """Análise do corte, em cache por (início, fim, revisão): não refaz a leitura do áudio à toa."""
    key = f"{clip['start']:.3f}:{clip['end']:.3f}:{clip.get('revision', 1)}:{len(p.get('transcript') or [])}"
    cache = _plan_cache(project_id, clip)
    with contextlib.suppress(OSError, ValueError, KeyError):
        data = json.loads(cache.read_text(encoding='utf-8'))
        if data['key'] == key:
            return data['analysis']
    work = Path(tempfile.mkdtemp(prefix='autocortes-direcao-'))
    try:
        source = storage.project_dir(project_id) / p['filename']
        wav = direction.extract_clip_audio(source, p['media'], clip['start'], clip['end'], work / 'trecho.wav')
        analysis = direction.analyze(p.get('transcript') or [], clip['start'], clip['end'],
                                     scenes=p.get('scenes') or [], wav_path=wav)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({'key': key, 'analysis': analysis}, ensure_ascii=False), encoding='utf-8')
    return analysis


def _plans_stale(clip: dict) -> bool:
    meta = clip.get('edit_plans_meta') or {}
    return bool(clip.get('edit_plans')) and (abs(meta.get('start', -1) - clip['start']) > 0.01 or
                                              abs(meta.get('end', -1) - clip['end']) > 0.01)


def _plans_view(clip: dict) -> dict:
    plans = clip.get('edit_plans') or {}
    return {'plans': {name: {**plan, 'summary': direction.describe(plan)} for name, plan in plans.items()},
            'selected': clip.get('edit_plan_selected'), 'stale': _plans_stale(clip)}


PLAN_VARIANTS = {'dinamico': 'impacto', 'equilibrado': 'equilibrado', 'cinematografico': 'cinematografico'}


def create_edit_plans(project_id: str, clip_id: int, options: dict) -> dict:
    """Analisa o corte e gera um plano (ou as três versões). Não renderiza nada."""
    style = options.get('style') or 'auto'
    if style != 'auto' and style not in direction.STYLES:
        raise AppError('Estilo de edição inválido.', 400, 'bad_style')
    p = storage.read_project(project_id)
    _require_ready(p)
    clip = _find_clip(p, clip_id)
    analysis = _analysis_for(project_id, p, clip)
    intensity = float(options.get('intensity', 0.6))
    toggles = {k: bool(v) for k, v in (options.get('options') or {}).items() if k in direction.DEFAULT_OPTIONS}
    if options.get('variants'):
        plans = {name: direction.build_plan(analysis, st, intensity, toggles) for name, st in PLAN_VARIANTS.items()}
        recommended, why = direction.choose_style(analysis)
        selected = next((n for n, st in PLAN_VARIANTS.items() if st == recommended), None)
        if selected:
            plans[selected]['reason'] = 'Versão recomendada. ' + why
        else:
            # Ex.: entrevista → Podcast premium, que não é uma das três versões.
            selected = 'equilibrado'
            plans[selected]['reason'] = (f'O estilo indicado para este trecho é {direction.STYLES[recommended]} '
                                         f'(use esse preset em "Planejar edição"). {why} Entre as três versões, '
                                         'a equilibrada é a mais próxima.')
    else:
        plans = {'principal': direction.build_plan(analysis, style, intensity, toggles)}
        selected = 'principal'
    with storage.edit_project(project_id) as p:
        clip = _find_clip(p, clip_id)
        clip['edit_plans'] = plans
        clip['edit_plan_selected'] = selected
        clip['edit_plans_meta'] = {'start': clip['start'], 'end': clip['end'], 'created_at': time.time()}
        return _plans_view(clip)


def get_edit_plans(project_id: str, clip_id: int) -> dict:
    return _plans_view(_find_clip(storage.read_project(project_id), clip_id))


def update_edit_plan(project_id: str, clip_id: int, name: str, changes: dict) -> dict:
    """Liga/desliga ações e escolhe a versão usada na exportação."""
    with storage.edit_project(project_id) as p:
        clip = _find_clip(p, clip_id)
        plan = (clip.get('edit_plans') or {}).get(name)
        if plan is None:
            raise AppError('Plano de edição não encontrado. Gere o plano novamente.', 404, 'not_found')
        for act in plan['actions']:
            value = (changes.get('actions') or {}).get(str(act['id']))
            if value is not None:
                act['enabled'] = bool(value)
        if changes.get('selected'):
            clip['edit_plan_selected'] = name
        plan['version'] = int(plan.get('version', 1)) + 1
        return _plans_view(clip)


def _filter_id(value) -> str | None:
    if not value or value == 'nenhum':
        return None
    filters.chain(value)   # valida
    return value


def request_export(project_id: str, clip_id: int, options: dict, *, preview: bool = False) -> dict:
    fmt = options.get('format') or ('9:16' if options.get('portrait', True) else '16:9')
    if fmt not in media.FORMATS:
        raise AppError('Formato inválido. Use 9:16, 16:9, 1:1 ou original.', 400, 'bad_format')
    framing = options.get('framing') or 'fit'
    if framing not in media.FRAMINGS:
        raise AppError('Enquadramento inválido.', 400, 'bad_framing')
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        clip = _find_clip(p, clip_id)
        review = p.get('rights') or {}
        if not preview and review.get('requires_acknowledgement') and not options.get('acknowledge_rights'):
            raise AppError('Há riscos relevantes de direitos neste projeto. Leia os alertas e confirme que está '
                           'ciente antes de exportar.', 409, 'rights_ack_required')
        if not preview and options.get('acknowledge_rights') and review.get('requires_acknowledgement'):
            p.setdefault('rights_acknowledgements', []).append({'at': time.time(), 'clip_id': clip_id,
                                                                'level': review.get('level')})
        plan_name = options.get('edit_plan')
        plan = None
        if plan_name:
            plan = (clip.get('edit_plans') or {}).get(plan_name)
            if plan is None:
                raise AppError('Plano de edição não encontrado. Gere o plano novamente.', 404, 'not_found')
            if _plans_stale(clip):
                raise AppError('Os tempos do corte mudaram depois que o plano foi gerado. Gere o plano de novo.',
                               409, 'plan_stale')
            # O plano decide enquadramento, estilo de legenda, tratamento de áudio e pausas. O enquadramento
            # escolhido no editor (``keep_framing``) vale sobre o do plano.
            options = {**options, 'framing': options['framing'] if options.get('keep_framing') and
                       options.get('framing') in media.FRAMINGS else plan['settings']['framing'],
                       'subtitle_style': plan['settings']['subtitle_style'],
                       'normalize_audio': plan['settings']['normalize_audio'], 'shorten_pauses': False}
            framing = options['framing']
        subtitle_style = options.get('subtitle_style') or 'classica'
        if subtitle_style not in subtitles.SUBTITLE_STYLES:
            raise AppError('Estilo de legenda inválido.', 400, 'bad_input')
        hook = bool(options.get('hook', False))
        hook_text = None
        if hook:
            hook_text = clip.get('hook_text') or (clip.get('title') if clip.get('selection_mode') != 'temporal' else None)
        params = {'clip_id': clip_id, 'format': fmt, 'framing': framing,
                  'subtitles': bool(options.get('subtitles', True)),
                  'subtitle_style': subtitle_style, 'hook': bool(hook_text), 'hook_text': hook_text,
                  'refine_subtitles': bool(options.get('refine_subtitles', True)),
                  'normalize_audio': bool(options.get('normalize_audio', False)),
                  'filter': _filter_id(options.get('filter')),
                  'shorten_pauses': bool(options.get('shorten_pauses', False)),
                  'autopilot': bool(options.get('autopilot', False)),
                  'autopilot_run_id': ((p.get('autopilot') or {}).get('run_id')
                                       if options.get('autopilot') else None),
                  'start': clip['start'], 'end': clip['end'], 'title': clip['title'],
                  'revision': clip.get('revision', 1)}
        if plan is not None:
            params['edit_plan'] = copy.deepcopy(plan)
            params['edit_plan_name'] = plan_name
        if clip.get('focus_rect') and framing in ('streamer', 'split', 'mix'):
            params['focus_rect'] = list(clip['focus_rect'])   # área do streamer desenhada à mão
        if framing == 'mix':
            # Por trecho: usa os trechos marcados no corte; sem trechos, a variação automática.
            clip_len = clip['end'] - clip['start']
            params['framing_segments'] = (clean_framing_segments(clip.get('framing_segments'), clip_len)
                                          or auto_framing_segments(p, clip))
        if preview:
            params['preview'] = True
    key = 'export:' + project_id + ':' + hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    job, created = _jobs().submit(project_id, 'export', params, dedupe_key=key)
    return public_job(job) | {'duplicate': not created}


def preview_path(project_id: str, clip_id: int) -> Path:
    p = storage.read_project(project_id)
    record = _find_clip(p, clip_id).get('preview') or {}
    name = record.get('file') or ''
    if not re.fullmatch(r'clip_\d{2,}_(9x16|16x9|1x1|orig)_v\d+\.mp4', name):
        raise AppError('Gere a prévia deste corte primeiro.', 404, 'no_preview')
    path = storage.project_dir(project_id) / 'preview' / name
    if not path.is_file():
        raise AppError('A prévia não está mais disponível. Gere de novo.', 404, 'no_preview')
    return path


def export_all(project_id: str, options: dict) -> dict:
    """Exporta vários cortes em vários formatos de uma vez (cada um vira uma tarefa na fila)."""
    formats = options.get('formats') or ['9:16']
    if not isinstance(formats, list) or not formats or any(f not in media.FORMATS for f in formats):
        raise AppError('Formatos inválidos.', 400, 'bad_format')
    p = storage.read_project(project_id)
    _require_ready(p)
    clip_ids = options.get('clip_ids') or [c['id'] for c in p.get('clips', [])]
    min_score = options.get('min_score')
    jobs, errors = [], []
    for clip in p.get('clips', []):
        if clip['id'] not in clip_ids:
            continue
        if min_score is not None and (clip.get('score') is None or clip['score'] < min_score):
            continue
        for fmt in dict.fromkeys(formats):
            single = {k: v for k, v in options.items() if k not in ('formats', 'clip_ids', 'min_score')}
            single['format'] = fmt
            try:
                jobs.append(request_export(project_id, clip['id'], single))
            except AppError as exc:
                errors.append({'clip_id': clip['id'], 'format': fmt, 'error': exc.message, 'code': exc.code})
    if not jobs and errors:
        raise AppError(errors[0]['error'], 409, errors[0]['code'])
    return {'jobs': jobs, 'errors': errors}


def get_settings() -> dict:
    return settings.public()


def update_settings(changes: dict) -> dict:
    settings.update(changes)
    return settings.public()


def autopilot_resume(project_id: str, acknowledge: bool) -> dict:
    return autopilot.resume(project_id, acknowledge)


def autopilot_run(project_id: str) -> dict:
    return autopilot.run_now(project_id)


def publication_text(project_id: str, clip_id: int, fmt: str) -> tuple[Path, str]:
    p = storage.read_project(project_id)
    clip = _find_clip(p, clip_id)
    record = (clip.get('exports') or {}).get(fmt)
    if not record or not record.get('package_file'):
        raise AppError('Pacote de publicação não encontrado. Exporte o corte primeiro.', 404, 'not_found')
    name = record['package_file']
    if not re.fullmatch(r'clip_\d{2,}_(9x16|16x9|1x1|orig)_v\d+\.txt', name):
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    path = storage.project_dir(project_id) / 'clips' / name
    if not path.is_file():
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    return path, safe_download_name(record.get('title') or clip['title'], f"_{media.FORMAT_SLUG[fmt]}_publicacao.txt")


def reanalyze(project_id: str) -> dict:
    """Analisa o vídeo de novo, do zero (transcrição, cenas e seleção). Num projeto pronto, os cortes atuais
    são substituídos ao terminar; se a nova análise falhar ou for cancelada, eles continuam valendo."""
    if _jobs().for_project(project_id, active_only=True):
        raise AppError('Aguarde as tarefas em andamento deste projeto terminarem.', 409, 'busy')
    return _queue_analysis(project_id, {}, from_status=('error', 'cancelled', 'interrupted', 'ready'))


def _queue_analysis(project_id: str, params: dict, *, from_status: tuple[str, ...]) -> dict:
    with storage.edit_project(project_id) as p:
        if p.get('status') not in from_status:
            raise AppError('Este projeto não pode ser analisado de novo agora.', 409, 'bad_state')
        if not (storage.project_dir(project_id) / p['filename']).is_file():
            raise AppError('O vídeo deste projeto não está mais na pasta; envie o arquivo de novo.', 409, 'no_source')
        p['status'] = 'queued'
        p['error'] = None
    job, _ = _jobs().submit(project_id, 'analysis', dict(params), dedupe_key=f'analysis:{project_id}')
    with storage.edit_project(project_id) as p:
        p['analysis_job'] = job['id']
    return public_job(job)


def reselect(project_id: str) -> dict:
    """Refaz só a escolha dos cortes sobre a transcrição já salva, com as Configurações atuais de curadoria
    (ex.: IA ativada depois da análise). Os cortes atuais são substituídos ao terminar."""
    if _jobs().for_project(project_id, active_only=True):
        raise AppError('Aguarde as tarefas em andamento deste projeto terminarem.', 409, 'busy')
    with storage.edit_project(project_id) as p:
        if p.get('status') != 'ready':
            raise AppError('A seleção só pode ser refeita em projetos prontos.', 409, 'bad_state')
        if (p.get('transcription') or {}).get('state') != 'ok' or not p.get('transcript'):
            raise AppError('Este projeto não tem transcrição salva; use Reanalisar.', 409, 'no_transcript')
        p['curation_profile'] = copy.deepcopy(settings.load()['curation'])
        p['status'] = 'queued'
        p['error'] = None
    job, _ = _jobs().submit(project_id, 'analysis', {'reselect': True}, dedupe_key=f'analysis:{project_id}')
    with storage.edit_project(project_id) as p:
        p['analysis_job'] = job['id']
    return public_job(job)


# --------------------------------------------------------------------------- duplicados e pastas

QUICK_BLOCK = 4 * 1024 * 1024


def quick_fingerprint(path: Path) -> str:
    """Impressão rápida do vídeo: tamanho + SHA-256 de três blocos de 4 MB (início, meio e fim).

    O navegador calcula a mesma coisa antes do envio (``studio.js``, ``quickFingerprint``), então dá para avisar
    que o vídeo já está num projeto sem enviar nem ler 20 GB. Arquivos de até 12 MB entram inteiros.
    """
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        if size <= 3 * QUICK_BLOCK:
            digest.update(handle.read())
        else:
            for offset in (0, size // 2 - QUICK_BLOCK // 2, size - QUICK_BLOCK):
                handle.seek(offset)
                digest.update(handle.read(QUICK_BLOCK))
    return f'quick:{size}:{digest.hexdigest()}'


def find_duplicate(size: int, quick: str) -> dict | None:
    """Projeto que já tem este vídeo (mesmo tamanho e mesma impressão rápida), ou ``None``."""
    if not re.fullmatch(r'quick:\d+:[0-9a-f]{64}', quick or '') or not quick.startswith(f'quick:{int(size)}:'):
        raise AppError('Impressão do vídeo inválida.', 400, 'bad_input')
    for item in storage.list_projects():
        try:
            p = storage.read_project(item['id'])
            source = storage.project_dir(item['id']) / p['filename']
            if source.stat().st_size != int(size):
                continue
            saved = p.get('quick_fingerprint')
            if not saved:
                saved = quick_fingerprint(source)
                with storage.edit_project(item['id']) as current:
                    current['quick_fingerprint'] = saved
        except (AppError, OSError, KeyError):
            continue
        if saved == quick:
            return {'id': item['id'], 'name': item['name'], 'status': item.get('status'), 'clips': item.get('clips'),
                    'size_bytes': int(size)}
    return None


def open_folder(project_id: str | None = None) -> dict:
    """Abre no Explorador a pasta dos arquivos exportados (do projeto, ou a pasta de saída geral).

    Só pastas do próprio app: a pasta de saída do projeto (cópias com nome legível) ou, sem ela, a pasta de
    cortes do projeto. Funciona apenas com o app rodando no computador do usuário (servidor local).
    """
    if project_id:
        p = storage.read_project(project_id)
        folder = publish.output_folder(p)
        if not folder.is_dir():
            folder = storage.project_dir(project_id) / 'clips'
    else:
        folder = config.OUTPUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    _reveal(folder)
    return {'folder': str(folder)}


def _reveal(folder: Path) -> None:
    import subprocess
    import sys
    try:
        if os.name == 'nt':
            os.startfile(str(folder))  # noqa: S606 (pasta do próprio app, servidor só local)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(folder)])
        else:
            subprocess.Popen(['xdg-open', str(folder)])
    except OSError as exc:
        raise AppError(f'Não foi possível abrir a pasta: {folder}', 500, 'open_failed') from exc


def delete_project(project_id: str) -> None:
    storage.project_dir(project_id)
    if _jobs().for_project(project_id, active_only=True):
        raise AppError('Cancele as tarefas em andamento antes de excluir o projeto.', 409, 'busy')
    storage.delete_project_dir(project_id)


# Caches que o app refaz sozinho quando faltam: miniaturas, onda do áudio, energia, faixas pretas, planos de
# enquadramento e prévias. Vídeo, transcrição, cortes e exportações nunca entram aqui.
CACHE_DIRS = {'cache': 'Miniaturas, onda e medidas do vídeo', 'direction': 'Planos de enquadramento',
              'preview': 'Prévias da edição'}


def _dir_size(folder: Path) -> tuple[int, int]:
    size = count = 0
    for path in folder.rglob('*') if folder.is_dir() else ():
        with contextlib.suppress(OSError):
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
                count += 1
    return size, count


def cache_usage(project_id: str) -> dict:
    folder = storage.project_dir(project_id)
    parts = []
    for name, label in CACHE_DIRS.items():
        size, count = _dir_size(folder / name)
        parts.append({'id': name, 'label': label, 'bytes': size, 'files': count})
    return {'parts': parts, 'total_bytes': sum(x['bytes'] for x in parts)}


def clear_cache(project_id: str) -> dict:
    """Apaga os caches do projeto (refeitos quando necessário). Recusa com tarefa em andamento no projeto."""
    folder = storage.project_dir(project_id)
    if _jobs().for_project(project_id, active_only=True):
        raise AppError('Espere as tarefas do projeto terminarem (ou cancele) antes de limpar o cache.', 409, 'busy')
    freed = 0
    for name in CACHE_DIRS:
        target = folder / name
        if target.is_symlink() or not target.is_dir():
            continue
        for path in target.iterdir():
            size = _dir_size(path)[0] if path.is_dir() and not path.is_symlink() else 0
            with contextlib.suppress(OSError):
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                    freed += size
                else:
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
    with storage.edit_project(project_id) as p:
        for clip in p.get('clips') or []:   # as prévias apagadas não podem continuar apontadas nos cortes
            clip.pop('preview', None)
    return {'freed_bytes': freed, **cache_usage(project_id)}


# --------------------------------------------------------------------------- arquivos

def source_path(project_id: str) -> tuple[Path, str]:
    p = storage.read_project(project_id)
    path = storage.project_dir(project_id) / p['filename']
    if not path.is_file():
        raise AppError('Vídeo não encontrado.', 404, 'not_found')
    return path, p['name']


def safe_download_name(title: str, suffix: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', title or 'corte').strip(' .')[:80] or 'corte'
    return f'{base}{suffix}'


def export_path(project_id: str, clip_id: int, fmt: str | None, kind: str = 'mp4') -> tuple[Path, str]:
    p = storage.read_project(project_id)
    clip = _find_clip(p, clip_id)
    exports = clip.get('exports') or {}
    record = exports.get(fmt) if fmt else (clip.get('export_metadata') or None)
    if not record:
        raise AppError('Exporte o trecho neste formato antes de baixar.', 404, 'not_exported')
    name = record.get('file') if kind == 'mp4' else record.get('srt_file')
    if not name or not re.fullmatch(r'clip_\d{2,}_(9x16|16x9|1x1|orig)_v\d+\.(mp4|srt)', name):
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    path = storage.project_dir(project_id) / 'clips' / name
    if not path.is_file():
        raise AppError('Arquivo não encontrado. Exporte novamente.', 404, 'not_found')
    slug = media.FORMAT_SLUG.get(record.get('format'), 'corte')
    return path, safe_download_name(record.get('title') or clip['title'], f'_{slug}.{kind}')


def delete_export(project_id: str, clip_id: int, fmt: str) -> dict:
    """Exclui uma exportação concluída do corte (MP4, legenda e pacote em ``clips/``). As cópias já feitas na pasta
    de saída não são apagadas: são arquivos do usuário fora do projeto."""
    folder = storage.project_dir(project_id)
    busy = [j for j in _jobs().for_project(project_id, active_only=True)
            if j.get('kind') == 'export' and (j.get('params') or {}).get('clip_id') == clip_id
            and (j.get('params') or {}).get('format') == fmt]
    if busy:
        raise AppError('Este corte está sendo exportado neste formato; espere terminar ou cancele.', 409, 'busy')
    with storage.edit_project(project_id) as p:
        clip = _find_clip(p, clip_id)
        record = (clip.get('exports') or {}).pop(fmt, None)
        if record is None:
            raise AppError('Exportação não encontrada.', 404, 'not_exported')
        if (clip.get('export_metadata') or {}).get('file') == record.get('file'):
            latest = max((clip.get('exports') or {}).values(), key=lambda r: r.get('created_at') or 0, default=None)
            clip['export_file'] = latest.get('file') if latest else None
            clip['export_metadata'] = latest
            if latest is None:
                clip.pop('export_file', None)
                clip.pop('export_metadata', None)
    freed = 0
    for name in filter(None, (record.get('file'), record.get('srt_file'), record.get('package_file'))):
        path = folder / 'clips' / name
        if path.parent == folder / 'clips' and path.is_file():   # só nomes simples dentro de clips/
            with contextlib.suppress(OSError):
                size = path.stat().st_size
                path.unlink()
                freed += size
    return {'clip_id': clip_id, 'format': fmt, 'freed_bytes': freed,
            'output_kept': bool((record.get('output') or {}).get('files'))}


def report(project_id: str) -> dict:
    p = storage.read_project(project_id)
    if p.get('status') != 'ready':
        raise AppError('Relatório disponível após a análise.', 409, 'not_ready')
    return {
        'gerado_em': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'aviso': 'Análise preventiva baseada em declarações do usuário. Não é parecer jurídico nem consulta ao Content ID.',
        'project': p['name'], 'media': p['media'],
        'rights_declarations': {k: p.get(k) for k in ('source_rights', 'external_audio', 'graphics_rights',
                                                      'third_party_footage', 'monetization', 'license_notes')},
        'analysis': p.get('rights'), 'rights_acknowledgements': p.get('rights_acknowledgements', []),
        'cloud_consents': p.get('cloud_consents', []),
        'transcription': p.get('transcription'), 'selection': p.get('selection'), 'ranking': p.get('ranker'),
        'curation': p.get('curation'), 'warnings': p.get('warnings', []),
        'clips': [{k: c.get(k) for k in ('id', 'start', 'end', 'score', 'selection_mode', 'title', 'reasons')} |
                  {'exports': {f: {k: r.get(k) for k in ('file', 'width', 'height', 'subtitles_burned', 'warnings')} |
                               {'validado': (r.get('validation') or {}).get('valid')}
                               for f, r in (c.get('exports') or {}).items()}}
                  for c in p.get('clips', [])],
    }


def health() -> dict:
    caps = media.ffmpeg_capabilities()
    free = storage.free_bytes()
    return {'ok': caps['ffmpeg'] and caps['ffprobe'], 'version': config.VERSION, 'mode': 'local', 'bind': '127.0.0.1',
            'ffmpeg': caps, 'transcription': transcription.available(),
            'whisper_model': config.WHISPER_MODEL if transcription.available() else None,
            'curation': curation.status(),
            'face_tracking': reframe.available(),
            'automation': {'watch': watcher.status(), 'output_dir': str(config.OUTPUT_DIR),
                           'autopilot_default': settings.load()['autopilot']['enabled']},
            'free_disk_gb': round(free / 1024 ** 3, 1),
            'limits': {'max_upload_gb': round(config.MAX_UPLOAD_BYTES / 1024 ** 3, 1),
                       'max_source_hours': config.MAX_SOURCE_SECONDS // 3600,
                       'analysis_workers': config.ANALYSIS_WORKERS, 'export_workers': config.EXPORT_WORKERS}}
