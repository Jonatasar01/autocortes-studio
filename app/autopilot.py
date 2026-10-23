"""Piloto automático: depois da análise, escolhe os melhores cortes e os exporta sozinho
(opcionalmente com roteiro e voz do ChatGPT), registrando cada decisão no projeto.

Limites deliberados:
- Risco alto de direitos PAUSA o piloto: só o usuário pode confirmar que leu os alertas.
- Cortes temporais (sem análise de conteúdo) só entram se o usuário permitir nas configurações.
- Narração automática exige OpenAI configurada e consentimento permanente registrado nas configurações.
"""
from __future__ import annotations

import copy
import logging
import time
import uuid
import threading
from functools import wraps

from . import narration, settings, storage
from .errors import AppError

log = logging.getLogger('autocortes.autopilot')

# Apenas decisões/submissões; os trabalhos de mídia continuam nos workers.
_decision_lock = threading.RLock()


def _serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _decision_lock:
            return fn(*args, **kwargs)
    return wrapped

STATUS_LABELS = {
    'aguardando_analise': 'Aguardando a análise',
    'executando': 'Exportando automaticamente',
    'pausado_direitos': 'Pausado: risco de direitos — revise e confirme para continuar',
    'sem_cortes': 'Nenhum corte atendeu aos critérios',
    'concluido': 'Concluído',
    'concluido_com_falhas': 'Concluído com falhas',
    'cancelado': 'Cancelado',
}


def snapshot(enabled: bool | None = None) -> dict | None:
    """Congela o perfil de automação no momento do envio (mudanças futuras não afetam o projeto)."""
    s = settings.load()
    ap = copy.deepcopy(s['autopilot'])
    if not (ap['enabled'] if enabled is None else enabled):
        return None
    ap['enabled'] = True
    return {'enabled': True, 'config': ap, 'status': 'aguardando_analise', 'events': [], 'selected': [],
            'created_at': time.time(), 'run_id': uuid.uuid4().hex}


def _event(p: dict, message: str, level: str = 'info') -> None:
    events = p['autopilot'].setdefault('events', [])
    events.append({'at': time.time(), 'level': level, 'message': message})
    del events[:-60]


def _set_status(p: dict, status: str, message: str | None = None, level: str = 'info') -> None:
    p['autopilot']['status'] = status
    p['autopilot']['status_label'] = STATUS_LABELS.get(status, status)
    if message:
        _event(p, message, level)


def choose(clips: list[dict], cfg: dict) -> tuple[list[dict], list[str]]:
    chosen, skipped = [], []
    for clip in sorted(clips, key=lambda c: c.get('rank') or c['id']):
        if clip.get('selection_mode') == 'temporal' or clip.get('score') is None:
            if not cfg['include_temporal']:
                skipped.append(f"Corte {clip['id']:02d}: temporal (sem análise de conteúdo) — não incluído.")
                continue
        elif clip['score'] < cfg['min_score']:
            skipped.append(f"Corte {clip['id']:02d}: nota {clip['score']} abaixo do mínimo {cfg['min_score']}.")
            continue
        chosen.append(clip)
        if len(chosen) >= cfg['max_clips']:
            break
    return chosen, skipped


@_serialized
def start(project_id: str) -> None:
    """Chamado ao fim da análise (ou manualmente). Decide o que exportar e enfileira as tarefas."""
    with storage.edit_project(project_id) as p:
        ap = p.get('autopilot')
        if not ap or not ap.get('enabled') or p.get('status') != 'ready':
            return
        if (p.get('rights') or {}).get('requires_acknowledgement') and not ap.get('rights_acknowledged'):
            _set_status(p, 'pausado_direitos', 'Risco relevante de direitos detectado. O piloto automático não confirma '
                        'riscos sozinho: revise a tabela de direitos e clique em "Continuar piloto automático".', 'warning')
            return
        cfg = ap['config']
        chosen, skipped = choose(p.get('clips', []), cfg)
        for message in skipped:
            _event(p, message)
        if not chosen:
            _set_status(p, 'sem_cortes', 'Nenhum corte atendeu aos critérios (nota mínima/cortes temporais). '
                        'Ajuste as configurações ou exporte manualmente.', 'warning')
            return
        ap['selected'] = [c['id'] for c in chosen]
        ap['dispatching'] = True
        run_id = ap.get('run_id')
        _set_status(p, 'executando', f"Selecionados {len(chosen)} corte(s): "
                    + ', '.join(f"{c['id']:02d}" for c in chosen) + '.')
    try:
        _dispatch(project_id)
    finally:
        with storage.edit_project(project_id) as p:
            if (p.get('autopilot') or {}).get('run_id') == run_id:
                p['autopilot']['dispatching'] = False
        refresh(project_id)


def _use_narration(cfg: dict) -> bool:
    nr = cfg['narration']
    return bool(nr['enabled'] and nr['consent'] and
                settings.load()['autopilot']['narration']['consent'] and narration.is_configured())


def _dispatch(project_id: str) -> None:
    from . import service
    p = storage.read_project(project_id)
    ap = p['autopilot']
    cfg = ap['config']
    use_narration = _use_narration(cfg)
    messages: list[tuple[str, str]] = []
    if cfg['narration']['enabled'] and not use_narration:
        messages.append(('Narração automática ignorada: OpenAI não configurada (OPENAI_API_KEY) ou sem '
                         'consentimento registrado. Exportando sem narração.', 'warning'))
    for clip_id in ap['selected']:
        try:
            if use_narration:
                nr = cfg['narration']
                service.request_narration(project_id, clip_id, {
                    'style': nr['style'], 'use_frames': nr['use_frames'], 'context': '', 'consent': True,
                    'autopilot': True, 'consent_source': 'Consentimento permanente nas Configurações'})
            else:
                submit_exports(project_id, clip_id, with_narration=False)
        except AppError as exc:
            messages.append((f'Corte {clip_id:02d}: {exc.message}', 'error'))
            with storage.edit_project(project_id) as current:
                current['autopilot'].setdefault('submission_errors', []).append(clip_id)
    if messages:
        with storage.edit_project(project_id) as p:
            for message, level in messages:
                _event(p, message, level)
    refresh(project_id)


def submit_exports(project_id: str, clip_id: int, *, with_narration: bool) -> None:
    from . import service
    p = storage.read_project(project_id)
    ap = p['autopilot']
    cfg = ap['config']
    nr = cfg['narration']
    existing = {j['params'].get('format') for j in service._jobs().for_project(project_id, limit=400)
                if j['kind'] == 'export' and j['params'].get('autopilot') and
                j['params'].get('clip_id') == clip_id and ap.get('run_id') and
                j['params'].get('autopilot_run_id') == ap['run_id']}
    for fmt in cfg['formats']:
        if fmt in existing:
            continue
        service.request_export(project_id, clip_id, {
            'format': fmt, 'framing': cfg['framing'], 'subtitles': cfg['subtitles'],
            'subtitle_style': cfg.get('subtitle_style', 'classica'), 'hook': cfg.get('hook', False),
            'normalize_audio': cfg['normalize_audio'], 'shorten_pauses': cfg['shorten_pauses'] and not with_narration,
            'narration': with_narration, 'narration_consent': with_narration, 'voice': nr['voice'],
            'original_volume': nr['original_volume'] if with_narration else 100,
            'subtitle_source': nr['subtitle_source'] if with_narration else 'fala',
            'acknowledge_rights': bool(ap.get('rights_acknowledged')), 'autopilot': True})


@_serialized
def after_job(kind: str, ctx, status: str, error: str | None) -> None:
    """Encadeia as etapas: roteiro pronto → exportações; atualiza o estado final."""
    if not ctx.params.get('autopilot'):
        return
    pid, clip_id = ctx.project_id, ctx.params.get('clip_id')
    current = storage.read_project(pid).get('autopilot') or {}
    if current.get('run_id') != ctx.params.get('autopilot_run_id'):
        return  # callback atrasado de uma execução anterior
    if kind == 'narration' and ctx.id in current.get('handled_narrations', []):
        refresh(pid)
        return
    try:
        if kind == 'narration':
            p = storage.read_project(pid)
            cfg = (p.get('autopilot') or {}).get('config') or {}
            if status == 'done':
                submit_exports(pid, clip_id, with_narration=True)
            elif status == 'failed' and cfg.get('narration', {}).get('fallback_without_narration', True):
                with storage.edit_project(pid) as p:
                    _event(p, f'Corte {clip_id:02d}: roteiro não gerado ({error}). Exportando sem narração.', 'warning')
                submit_exports(pid, clip_id, with_narration=False)
            else:
                with storage.edit_project(pid) as p:
                    _event(p, f'Corte {clip_id:02d}: roteiro {status} ({error or "sem detalhes"}).', 'warning')
        elif kind == 'export' and status != 'done':
            with storage.edit_project(pid) as p:
                _event(p, f"Corte {clip_id:02d} ({ctx.params.get('format')}): exportação {status}. {error or ''}".strip(),
                       'error' if status == 'failed' else 'warning')
    except AppError as exc:
        with storage.edit_project(pid) as p:
            _event(p, f'Corte {clip_id:02d}: {exc.message}', 'error')
            p['autopilot'].setdefault('submission_errors', []).append(clip_id)
    if kind == 'narration':
        with storage.edit_project(pid) as p:
            if (p.get('autopilot') or {}).get('run_id') == ctx.params.get('autopilot_run_id'):
                handled = p['autopilot'].setdefault('handled_narrations', [])
                if ctx.id not in handled:
                    handled.append(ctx.id)
    refresh(pid)


@_serialized
def refresh(project_id: str) -> None:
    from . import service
    with storage.edit_project(project_id) as p:
        ap = p.get('autopilot')
        if not ap or ap.get('status') != 'executando' or ap.get('dispatching'):
            return
        try:
            jobs = service._jobs().for_project(project_id, limit=400)
        except AppError:
            return
        mine = [j for j in jobs if j['params'].get('autopilot') and
                (j['params'].get('autopilot_run_id') == ap['run_id'] if ap.get('run_id') else
                 not j['params'].get('autopilot_run_id') and j.get('created', 0) >= ap.get('created_at', 0))]
        if any(j['status'] in ('queued', 'running', 'cancelling') for j in mine):
            return
        if ap.get('run_id') and any(j['kind'] == 'narration' and
                                   j['id'] not in ap.get('handled_narrations', []) for j in mine):
            return  # o callback ainda pode enfileirar exportações/fallback
        exports = [j for j in mine if j['kind'] == 'export']
        done = sum(1 for j in exports if j['status'] == 'done')
        failed = sum(1 for j in exports if j['status'] in ('failed', 'interrupted'))
        formats = set(ap['config']['formats'])
        successful = {(j['params'].get('clip_id'), j['params'].get('format'))
                      for j in exports if j['status'] == 'done'}
        failed += sum(1 for j in mine if j['kind'] == 'narration' and
                      j['status'] in ('failed', 'interrupted') and
                      not all((j['params'].get('clip_id'), fmt) in successful for fmt in formats))
        failed += len(set(ap.get('submission_errors', [])))
        cancelled = sum(1 for j in mine if j['status'] == 'cancelled')
        expected = {(clip_id, fmt) for clip_id in ap.get('selected', []) for fmt in formats}
        if not failed and not cancelled and expected - successful:
            failed = len(expected - successful)
        if done and not failed and not cancelled:
            _set_status(p, 'concluido', f'{done} arquivo(s) exportado(s) e validado(s) automaticamente.')
        elif done or failed:
            _set_status(p, 'concluido_com_falhas', f'{done} exportado(s), {failed} com falha, {cancelled} cancelado(s).',
                        'warning')
        else:
            _set_status(p, 'cancelado', 'Tarefas do piloto automático canceladas.', 'warning')


@_serialized
def recover(project_id: str) -> None:
    """Finaliza callbacks persistidos antes de uma interrupção do processo."""
    from . import service
    from .jobs import JobContext
    with storage.edit_project(project_id) as p:
        ap = p.get('autopilot') or {}
        if ap.get('status') != 'executando' or not ap.get('run_id'):
            return
        ap['dispatching'] = False
        run_id = ap['run_id']
        handled = set(ap.get('handled_narrations', []))
    for job in service._jobs().for_project(project_id, limit=400):
        if (job['kind'] == 'narration' and job['params'].get('autopilot_run_id') == run_id and
                job['status'] in ('done', 'failed', 'cancelled', 'interrupted') and job['id'] not in handled):
            after_job('narration', JobContext(service._jobs(), job), job['status'], job.get('error'))
    refresh(project_id)


@_serialized
def resume(project_id: str, acknowledge: bool) -> dict:
    """Usuário revisou os direitos e autoriza o piloto a continuar."""
    if not acknowledge:
        raise AppError('Confirme que leu os alertas de direitos para continuar.', 409, 'rights_ack_required')
    with storage.edit_project(project_id) as p:
        ap = p.get('autopilot')
        if not ap:
            raise AppError('Este projeto não usa o piloto automático.', 409, 'no_autopilot')
        if ap.get('status') != 'pausado_direitos':
            raise AppError('O piloto automático não está pausado.', 409, 'bad_state')
        ap['rights_acknowledged'] = True
        p.setdefault('rights_acknowledgements', []).append({'at': time.time(), 'clip_id': None,
                                                            'level': (p.get('rights') or {}).get('level'),
                                                            'source': 'piloto automático'})
        _event(p, 'Usuário confirmou ciência dos riscos de direitos; piloto retomado.')
    start(project_id)
    return storage.read_project(project_id)['autopilot']


@_serialized
def run_now(project_id: str) -> dict:
    """Ativa o piloto num projeto já analisado, com o perfil atual das configurações."""
    from . import service
    if service._jobs().for_project(project_id, active_only=True):
        raise AppError('Aguarde as tarefas em andamento deste projeto.', 409, 'busy')
    snap = snapshot(enabled=True)
    with storage.edit_project(project_id) as p:
        if p.get('status') != 'ready':
            raise AppError('A análise ainda não foi concluída.', 409, 'not_ready')
        previous = p.get('autopilot') or {}
        snap['rights_acknowledged'] = bool(previous.get('rights_acknowledged'))
        p['autopilot'] = snap
        _event(p, 'Piloto automático iniciado manualmente com o perfil atual das configurações.')
    start(project_id)
    return storage.read_project(project_id)['autopilot']
