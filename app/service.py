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

from . import (autopilot, config, curation, direction, downloads, media, music, narration, pipeline, publish, reframe, rights, settings,
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
        'export': Handler(pipeline.run_export, config.EXPORT_WORKERS, _end_export,
                          retry_codes=RETRYABLE, max_retries=config.JOB_MAX_RETRIES),
        'narration': Handler(pipeline.run_narration, 1, _end_narration,
                             retry_codes=RETRYABLE, max_retries=config.JOB_MAX_RETRIES),
    })
    manager.start()
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
                    p['error'] = 'A análise foi interrompida. Use "Reprocessar" para tentar novamente.'
    return manager


RETRYABLE = {'openai_unreachable', 'openai_rate_limit', 'openai_unavailable'}


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


def _end_narration(ctx, status, error):
    autopilot.after_job('narration', ctx, status, error)


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
        'recognize_music': str(form.get('recognize_music_opt_in', '')).strip().lower() in truthy,
        'target_seconds': _int_range(form.get('target_seconds', 35), 10, 90, 'Duração por corte deve ficar entre 10 e 90 s.'),
        'desired_count': _int_range(form.get('desired_count', 5), 1, 15, 'Quantidade de cortes deve ficar entre 1 e 15.'),
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
            wants_autopilot = options.pop('autopilot')
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
                    'music_review': {'status': 'not_requested', 'message': 'Reconhecimento musical não executado.'},
                    'rights': rights.rights_review(
                        options['source_rights'], options['external_audio'], options['license_notes'],
                        info['audio'], options['graphics_rights'], options['third_party_footage'],
                        options['monetization']),
                })
            if self.options['recognize_music'] and self.editing_mode != 'manual':
                project['music_consent'] = {'granted_at': time.time(), 'provider': 'ACRCloud',
                                            'scope': f'até {config.ACRCLOUD_MAX_SAMPLES} amostras de 12 s de áudio'}
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


# --------------------------------------------------------------------------- consultas

def public_job(job: dict | None) -> dict | None:
    if not job:
        return None
    return {k: job.get(k) for k in ('id', 'kind', 'status', 'stage', 'progress', 'message', 'error',
                                    'created', 'started', 'finished')} | {
        'clip_id': job['params'].get('clip_id'), 'format': job['params'].get('format'),
        'narration': job['params'].get('narration'),
        'result': job.get('result')}


def get_project(project_id: str) -> dict:
    p = storage.read_project(project_id)
    p.pop('filename', None)
    jobs = _jobs().for_project(project_id, limit=60)
    p['jobs'] = [public_job(j) for j in jobs]
    analysis = next((j for j in jobs if j['id'] == p.get('analysis_job')), None)
    p['analysis'] = public_job(analysis)
    if analysis:
        p['analysis_events'] = _jobs().events(analysis['id'], 20)
    return p


def list_projects() -> list[dict]:
    return storage.list_projects()


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
        if preview:
            # A prévia fica no computador e não é publicada; narração (paga) não entra na prévia.
            options = {**options, 'narration': False}
        if not preview and review.get('requires_acknowledgement') and not options.get('acknowledge_rights'):
            raise AppError('Há riscos relevantes de direitos neste projeto. Leia os alertas e confirme que está '
                           'ciente antes de exportar.', 409, 'rights_ack_required')
        if not preview and options.get('acknowledge_rights') and review.get('requires_acknowledgement'):
            p.setdefault('rights_acknowledgements', []).append({'at': time.time(), 'clip_id': clip_id,
                                                                'level': review.get('level')})
        use_narration = bool(options.get('narration'))
        voice = options.get('voice') or config.OPENAI_VOICE
        if use_narration:
            if not (clip.get('narration') or {}).get('segments'):
                raise AppError('Gere o roteiro de narração deste corte antes de exportar com narração.', 409, 'no_script')
            if not narration.is_configured():
                raise AppError('Narração indisponível: defina OPENAI_API_KEY e reinicie o aplicativo.', 400,
                               'openai_not_configured')
            if voice not in narration.VOICES:
                raise AppError('Voz inválida.', 400, 'bad_voice')
            if not options.get('narration_consent'):
                raise AppError('Confirme o envio do roteiro à OpenAI para gerar a voz.', 409, 'consent_required')
            p.setdefault('openai_consents', []).append({
                'granted_at': time.time(), 'provider': 'OpenAI', 'clip_id': clip_id,
                'scope': 'síntese de voz', 'model': config.OPENAI_TTS_MODEL,
                'source': 'piloto automático (Configurações)' if options.get('autopilot') else 'pedido na interface'})
        plan_name = options.get('edit_plan')
        plan = None
        if plan_name:
            plan = (clip.get('edit_plans') or {}).get(plan_name)
            if plan is None:
                raise AppError('Plano de edição não encontrado. Gere o plano novamente.', 404, 'not_found')
            if _plans_stale(clip):
                raise AppError('Os tempos do corte mudaram depois que o plano foi gerado. Gere o plano de novo.',
                               409, 'plan_stale')
            # O plano decide enquadramento, estilo de legenda, tratamento de áudio e pausas.
            options = {**options, 'framing': plan['settings']['framing'],
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
        subtitle_source = options.get('subtitle_source') or ('narracao' if use_narration else 'fala')
        if subtitle_source not in ('fala', 'narracao'):
            raise AppError('Origem de legenda inválida.', 400, 'bad_input')
        params = {'clip_id': clip_id, 'format': fmt, 'framing': framing,
                  'subtitles': bool(options.get('subtitles', True)),
                  'subtitle_style': subtitle_style, 'hook': bool(hook_text), 'hook_text': hook_text,
                  'normalize_audio': bool(options.get('normalize_audio', False)),
                  'narration': use_narration, 'voice': voice if use_narration else None,
                  'original_volume': _int_range(options.get('original_volume', 100), 0, 100, 'Volume inválido.'),
                  'subtitle_source': subtitle_source,
                  'shorten_pauses': bool(options.get('shorten_pauses', False)) and not use_narration,
                  'autopilot': bool(options.get('autopilot', False)),
                  'autopilot_run_id': ((p.get('autopilot') or {}).get('run_id')
                                       if options.get('autopilot') else None),
                  'start': clip['start'], 'end': clip['end'], 'title': clip['title'],
                  'revision': clip.get('revision', 1)}
        if plan is not None:
            params['edit_plan'] = copy.deepcopy(plan)
            params['edit_plan_name'] = plan_name
        if preview:
            params['preview'] = True
        if use_narration:
            params['narration_script'] = copy.deepcopy(clip['narration'])
    key = 'export:' + project_id + ':' + hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    job, created = _jobs().submit(project_id, 'export', params, dedupe_key=key)
    return public_job(job) | {'duplicate': not created}


def preview_path(project_id: str, clip_id: int) -> Path:
    p = storage.read_project(project_id)
    record = _find_clip(p, clip_id).get('preview') or {}
    name = record.get('file') or ''
    if not re.fullmatch(r'clip_\d{2}_(9x16|16x9|1x1|orig)_v\d+\.mp4', name):
        raise AppError('Gere a prévia deste corte primeiro.', 404, 'no_preview')
    path = storage.project_dir(project_id) / 'preview' / name
    if not path.is_file():
        raise AppError('A prévia não está mais disponível. Gere de novo.', 404, 'no_preview')
    return path


def request_narration(project_id: str, clip_id: int, options: dict) -> dict:
    """Gera o roteiro com ChatGPT (tarefa na fila). Exige consentimento explícito para envio à OpenAI."""
    style = options.get('style') or 'recap'
    if style not in narration.STYLES:
        raise AppError('Estilo de narração inválido.', 400, 'bad_style')
    if not narration.is_configured():
        raise AppError('Narração indisponível: defina a variável OPENAI_API_KEY e reinicie o aplicativo.', 400,
                       'openai_not_configured')
    if not options.get('consent'):
        raise AppError('Confirme que autoriza o envio da transcrição do trecho (e dos quadros, se marcado) à OpenAI.',
                       409, 'consent_required')
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        clip = _find_clip(p, clip_id)
        params = {'clip_id': clip_id, 'style': style, 'use_frames': bool(options.get('use_frames')),
                  'context': _clean_text(options.get('context', ''), 500), 'start': clip['start'], 'end': clip['end'],
                  'revision': clip.get('revision', 1), 'autopilot': bool(options.get('autopilot', False))}
        params['autopilot_run_id'] = ((p.get('autopilot') or {}).get('run_id')
                                      if options.get('autopilot') else None)
        p.setdefault('openai_consents', []).append({
            'granted_at': time.time(), 'provider': 'OpenAI', 'clip_id': clip_id,
            'scope': 'roteiro e quadros' if params['use_frames'] else 'roteiro', 'model': config.OPENAI_MODEL,
            'source': _clean_text(options.get('consent_source') or 'pedido na interface', 120)})
    key = 'narration:{}:{clip_id}:{style}:{use_frames}:{start}:{end}'.format(project_id, **params) + hashlib.sha1(params['context'].encode()).hexdigest()[:12]
    if params['autopilot_run_id']:
        key += ':' + params['autopilot_run_id']
    job, created = _jobs().submit(project_id, 'narration', params, dedupe_key=key)
    return public_job(job) | {'duplicate': not created}


def update_narration(project_id: str, clip_id: int, segments: list) -> dict:
    """Salva o roteiro editado pelo usuário (validado como o da IA)."""
    with storage.edit_project(project_id) as p:
        _require_ready(p)
        clip = _find_clip(p, clip_id)
        script = clip.get('narration')
        if not script:
            raise AppError('Gere o roteiro antes de editá-lo.', 409, 'no_script')
        clean, warnings = narration.validate_segments(segments, clip['end'] - clip['start'])
        script.update({'segments': clean, 'warnings': warnings, 'edited_at': time.time(), 'edited_manually': True})
        return dict(script)


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
    if not re.fullmatch(r'clip_\d{2}_(9x16|16x9|1x1|orig)_v\d+\.txt', name):
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    path = storage.project_dir(project_id) / 'clips' / name
    if not path.is_file():
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    return path, safe_download_name(record.get('title') or clip['title'], f"_{media.FORMAT_SLUG[fmt]}_publicacao.txt")


def reanalyze(project_id: str) -> dict:
    with storage.edit_project(project_id) as p:
        if p.get('status') not in ('error', 'cancelled', 'interrupted'):
            raise AppError('Só é possível reprocessar análises com erro, canceladas ou interrompidas.', 409, 'bad_state')
        p['status'] = 'queued'
        p['error'] = None
    job, _ = _jobs().submit(project_id, 'analysis', {}, dedupe_key=f'analysis:{project_id}')
    with storage.edit_project(project_id) as p:
        p['analysis_job'] = job['id']
    return public_job(job)


def delete_project(project_id: str) -> None:
    storage.project_dir(project_id)
    if _jobs().for_project(project_id, active_only=True):
        raise AppError('Cancele as tarefas em andamento antes de excluir o projeto.', 409, 'busy')
    storage.delete_project_dir(project_id)


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
    if not name or not re.fullmatch(r'clip_\d{2}_(9x16|16x9|1x1|orig)_v\d+\.(mp4|srt)', name):
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    path = storage.project_dir(project_id) / 'clips' / name
    if not path.is_file():
        raise AppError('Arquivo não encontrado. Exporte novamente.', 404, 'not_found')
    slug = media.FORMAT_SLUG.get(record.get('format'), 'corte')
    return path, safe_download_name(record.get('title') or clip['title'], f'_{slug}.{kind}')


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
        'analysis': p.get('rights'), 'music_recognition': p.get('music_review'),
        'music_consent': p.get('music_consent'), 'rights_acknowledgements': p.get('rights_acknowledgements', []),
        'openai_consents': p.get('openai_consents', []),
        'transcription': p.get('transcription'), 'selection': p.get('selection'), 'ranking': p.get('ranker'),
        'warnings': p.get('warnings', []),
        'clips': [{k: c.get(k) for k in ('id', 'start', 'end', 'score', 'selection_mode', 'title', 'reasons')} |
                  {'narration': ({k: (c.get('narration') or {}).get(k) for k in
                                  ('style', 'model', 'title', 'segments', 'consent', 'edited_manually')}
                                 if c.get('narration') else None),
                   'exports': {f: {k: r.get(k) for k in ('file', 'width', 'height', 'subtitles_burned',
                                                          'ai_narration', 'narration', 'warnings')} |
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
            'ollama_enabled': config.OLLAMA_ENABLED, 'music_provider_configured': music.is_configured(),
            'openai_configured': narration.is_configured(),
            'curation': curation.status(),
            'face_tracking': reframe.available(),
            'automation': {'watch': watcher.status(), 'output_dir': str(config.OUTPUT_DIR),
                           'autopilot_default': settings.load()['autopilot']['enabled']},
            'narration': {'styles': {k: v['label'] for k, v in narration.STYLES.items()},
                          'voices': list(narration.VOICES), 'default_voice': config.OPENAI_VOICE,
                          'model': config.OPENAI_MODEL, 'tts_model': config.OPENAI_TTS_MODEL},
            'free_disk_gb': round(free / 1024 ** 3, 1),
            'limits': {'max_upload_gb': round(config.MAX_UPLOAD_BYTES / 1024 ** 3, 1),
                       'max_source_hours': config.MAX_SOURCE_SECONDS // 3600,
                       'analysis_workers': config.ANALYSIS_WORKERS, 'export_workers': config.EXPORT_WORKERS}}
