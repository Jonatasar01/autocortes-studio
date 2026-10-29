"""API local do AutoCortes (FastAPI). Camada fina: regras ficam em ``service``.

Executar somente em 127.0.0.1. Para acesso remoto seria necessário autenticação e TLS —
isso NÃO está implementado e por isso o bind padrão é local.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Literal, Optional
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.background import BackgroundTask

from . import config, dashboard, filters, service, timeline
from .config import ROOT  # noqa: F401  (compatibilidade)
from .errors import AppError
from .security import LocalSecurityMiddleware

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('autocortes')


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await run_in_threadpool(service.startup)
    try:
        yield
    finally:
        await run_in_threadpool(service.shutdown)


app = FastAPI(title='AutoCortes Local', lifespan=lifespan, redoc_url=None,
              docs_url='/api/docs' if config.ENABLE_API_DOCS else None,
              openapi_url='/api/openapi.json' if config.ENABLE_API_DOCS else None)
app.add_middleware(LocalSecurityMiddleware)


@app.exception_handler(AppError)
async def app_error(_request: Request, exc: AppError):
    return JSONResponse({'detail': exc.message, 'code': exc.code}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    fields = ', '.join('.'.join(str(p) for p in e.get('loc', [])[1:]) or 'corpo' for e in exc.errors()[:5])
    return JSONResponse({'detail': f'Dados inválidos: {fields}.', 'code': 'bad_input'}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception):
    log.exception('Erro inesperado na API')
    return JSONResponse({'detail': 'Erro interno inesperado. Consulte o terminal para detalhes.',
                         'code': 'internal'}, status_code=500)


# --------------------------------------------------------------------------- interface

@app.get('/', response_class=HTMLResponse)
def home():
    # no-cache: sem isso o navegador reaproveita a página antiga depois de uma atualização do app.
    return HTMLResponse((config.STATIC_DIR / 'studio.html').read_text(encoding='utf-8'),
                        headers={'Cache-Control': 'no-cache'})


@app.get('/classic')
def classic_editor():
    # Compatibility for saved links; the removed editor is not restored.
    return RedirectResponse('/', status_code=307)


@app.get('/static/{name}')
def static(name: str):
    if name not in {'favicon.svg', 'studio.css', 'studio.js'}:
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    # Revalida a cada carga (ETag): depois de uma atualização o navegador não fica com JS/CSS antigos.
    return FileResponse(config.STATIC_DIR / name, headers={'Cache-Control': 'no-cache'})


@app.get('/api/health')
def health():
    return service.health()


@app.get('/api/automation')
def automation_overview():
    from .automation import overview
    return overview()


@app.post('/api/automation/presets/{name}')
def automation_preset(name: str):
    from .automation import apply_preset
    return apply_preset(name)


class LinkImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=10, max_length=2048)
    options: dict[str, str | int | bool] = Field(default_factory=dict)


@app.post('/api/imports/link')
def import_link(options: LinkImport):
    from .automation import import_url
    return import_url(options.url, options.options)


@app.post('/api/jobs/{job_id}/retry')
def retry_task(job_id: str):
    from .automation import retry
    return retry(job_id)


@app.get('/api/projects/{project_id}/bundle')
def project_bundle(project_id: str):
    from .automation import bundle
    path = bundle(project_id)
    return FileResponse(path, media_type='application/zip', filename='autocortes-pacote.zip',
                        background=BackgroundTask(path.unlink, missing_ok=True))


class AdvancedRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acknowledge_rights: bool = False
    # Opcional: remove pausas longas pelas palavras da transcrição (transcript.set + removePauses no FilmCraft).
    remove_pauses: bool = False
    pause_min_seconds: float = Field(default=0.7, ge=0.3, le=5, allow_inf_nan=False)
    pause_keep_seconds: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)


@app.post('/api/projects/{project_id}/advanced')
def advanced_export(project_id: str, options: AdvancedRequest):
    from .advanced import request
    pause = ({'min_seconds': options.pause_min_seconds, 'keep_seconds': options.pause_keep_seconds}
             if options.remove_pauses else None)
    return request(project_id, options.acknowledge_rights, pause)


@app.get('/api/projects/{project_id}/advanced/{job_id}/{kind}')
def advanced_file(project_id: str, job_id: str, kind: str):
    from .advanced import artifact
    file = artifact(project_id, job_id, kind)
    return FileResponse(file, filename=file.name)


# --------------------------------------------------------------------------- projetos

@app.get('/api/projects')
def list_projects():
    return {'projects': service.list_projects()}


@app.post('/api/projects')
async def create_project(request: Request):
    """Aceita o vídeo como corpo bruto (recomendado: sem cópia temporária, limite real em fluxo)
    com os campos na query string, ou como multipart/form-data (compatibilidade)."""
    content_type = request.headers.get('content-type', '')
    if content_type.startswith('multipart/form-data'):
        form = await request.form(max_files=1, max_fields=30)
        try:
            upload = form.get('file')
            if upload is None or not hasattr(upload, 'read'):
                raise AppError('Envie um arquivo de vídeo no campo "file".', 400, 'bad_input')
            fields = {k: v for k, v in form.items() if isinstance(v, str)}

            def work():
                upload.file.seek(0)
                return service.create_project(iter(lambda: upload.file.read(4 * 1024 * 1024), b''),
                                              upload.filename, fields, upload.size)
            return await run_in_threadpool(work)
        finally:
            await form.close()
    try:
        declared = int(request.headers.get('content-length') or 0) or None
    except ValueError:
        declared = None
    filename = unquote(request.headers.get('x-filename', ''))
    upload = await run_in_threadpool(service.Upload, filename, dict(request.query_params), declared)
    buffer = bytearray()
    try:
        async for chunk in request.stream():
            buffer += chunk
            if len(buffer) >= 4 * 1024 * 1024:
                await run_in_threadpool(upload.write, bytes(buffer))
                buffer.clear()
        if buffer:
            await run_in_threadpool(upload.write, bytes(buffer))
    except ClientDisconnect:
        upload.abort()
        raise AppError('Envio interrompido.', 400, 'upload_aborted') from None
    except BaseException:
        upload.abort()
        raise
    return await run_in_threadpool(upload.finish)


@app.get('/api/projects/{project_id}/jobs')
def project_jobs(project_id: str):
    return {'jobs': service.project_jobs(project_id)}


@app.get('/api/projects/{project_id}')
def get_project(project_id: str, transcript: bool = False):
    """``?transcript=1`` inclui a transcrição completa (fica de fora por padrão: pode ter vários MB)."""
    return service.get_project(project_id, include_transcript=transcript)


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str):
    service.delete_project(project_id)
    return {'ok': True}


@app.get('/api/projects/{project_id}/cache')
def project_cache(project_id: str):
    """Espaço dos caches do projeto (miniaturas, onda, planos de enquadramento, prévias)."""
    return service.cache_usage(project_id)


@app.delete('/api/projects/{project_id}/cache')
def clear_project_cache(project_id: str):
    """Apaga só os caches refeitos sozinhos; vídeo, transcrição, cortes e exportações ficam."""
    return service.clear_cache(project_id)


@app.post('/api/projects/{project_id}/reanalyze')
def reanalyze(project_id: str):
    return service.reanalyze(project_id)


@app.post('/api/projects/{project_id}/reselect')
def reselect(project_id: str):
    return service.reselect(project_id)


@app.get('/api/projects/{project_id}/source')
def source_file(project_id: str):
    path, name = service.source_path(project_id)
    return FileResponse(path, media_type='video/mp4' if path.suffix in ('.mp4', '.m4v') else None,
                        filename=name, content_disposition_type='inline')


@app.get('/api/projects/{project_id}/filmstrip')
def project_filmstrip(project_id: str, n: int = 40):
    """Tira de miniaturas da linha do tempo (gerada uma vez, em cache)."""
    return FileResponse(timeline.filmstrip(project_id, n), media_type='image/jpeg',
                        headers={'Cache-Control': 'max-age=86400'})


@app.get('/api/dashboard')
def get_dashboard():
    """Página inicial: projetos, tarefas em andamento, últimas exportações e dependências."""
    return dashboard.overview()


@app.get('/api/jobs')
def list_jobs(limit: int = 30):
    """Tarefas de todos os projetos (as ativas primeiro), para o painel de processamentos."""
    return {'jobs': dashboard.jobs(max(1, min(100, limit)))}


@app.get('/api/projects/{project_id}/thumb')
def project_thumb(project_id: str):
    return FileResponse(timeline.thumbnail(project_id), media_type='image/jpeg',
                        headers={'Cache-Control': 'max-age=86400'})


@app.get('/api/filters')
def list_filters():
    return {'filters': filters.catalog()}


@app.get('/api/projects/{project_id}/filter-samples')
def filter_samples(project_id: str, t: float = 0.0):
    """Quadro do instante ``t`` em cada filtro, lado a lado (ordem de ``/api/filters``)."""
    return FileResponse(filters.sample_strip(project_id, t), media_type='image/jpeg',
                        headers={'Cache-Control': 'max-age=86400'})


@app.get('/api/projects/{project_id}/waveform')
def project_waveform(project_id: str):
    """Forma de onda do áudio; ``state: pending`` enquanto é gerada em segundo plano."""
    return timeline.waveform(project_id)


class ClipEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=120)


class ManualClipRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=120)


class SplitClipRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    at: float = Field(ge=0, allow_inf_nan=False)


@app.post('/api/projects/{project_id}/clips')
def create_clip(project_id: str, clip: ManualClipRequest):
    return service.create_manual_clip(project_id, clip.start, clip.end, clip.title)


@app.delete('/api/projects/{project_id}/clips/{clip_id}')
def delete_clip(project_id: str, clip_id: int):
    return service.delete_manual_clip(project_id, clip_id)


@app.post('/api/projects/{project_id}/clips/{clip_id}/split')
def split_clip(project_id: str, clip_id: int, body: SplitClipRequest):
    return service.split_manual_clip(project_id, clip_id, body.at)


class Feedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    verdict: Optional[Literal['bom', 'rejeitado']] = None
    reason: str = Field(default='', max_length=200)


@app.put('/api/projects/{project_id}/clips/{clip_id}/feedback')
def clip_feedback(project_id: str, clip_id: int, body: Feedback):
    """Corte bom ou rejeitado: referência local para medir e calibrar a seleção."""
    return service.set_feedback(project_id, clip_id, body.verdict, body.reason)


@app.patch('/api/projects/{project_id}/clips/{clip_id}')
def edit_clip(project_id: str, clip_id: int, edit: ClipEdit):
    return service.edit_clip(project_id, clip_id, edit.start, edit.end, edit.title)


class FramingSegment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    framing: Literal['fit', 'blur', 'track', 'streamer', 'split']


class FramingSegments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    segments: list[FramingSegment] = Field(max_length=200)


@app.put('/api/projects/{project_id}/clips/{clip_id}/framing')
def set_clip_framing(project_id: str, clip_id: int, body: FramingSegments):
    """Enquadramento por trecho: cada parte do corte com fundo desfocado, quadro inteiro ou seguir rosto."""
    return service.set_framing_segments(project_id, clip_id, [seg.model_dump() for seg in body.segments])


@app.post('/api/projects/{project_id}/clips/{clip_id}/framing/auto')
def auto_clip_framing(project_id: str, clip_id: int):
    """Variação automática: alterna seguir rosto e fundo desfocado a cada troca de cena."""
    return service.set_framing_segments(project_id, clip_id, None, auto=True)


class ExportOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    format: Optional[Literal['9:16', '16:9', '1:1', 'original']] = None
    portrait: bool = True  # compatibilidade: usado quando "format" não é enviado
    framing: Literal['fit', 'blur', 'track', 'streamer', 'split', 'mix'] = 'fit'   # mix = por trecho
    subtitles: bool = True
    subtitle_style: Literal['classica', 'dinamica'] = 'classica'
    refine_subtitles: bool = True   # legenda precisa: retranscreve o corte com o modelo maior (GPU)
    hook: bool = False
    normalize_audio: bool = False
    acknowledge_rights: bool = False
    shorten_pauses: bool = False
    filter: Optional[str] = Field(default=None, max_length=20)   # filtro de cor (app/filters.py)
    edit_plan: Optional[str] = Field(default=None, pattern=r'^[a-z_]{1,40}$')
    keep_framing: bool = False   # com plano de edição: usa o enquadramento enviado em vez do sugerido pelo plano


class PlanToggles(BaseModel):
    model_config = ConfigDict(extra='forbid')
    zoom: bool = True
    dynamic_subtitles: bool = True
    audio: bool = True
    denoise: bool = False
    remove_pauses: bool = True
    reframe: bool = True


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    style: Literal['auto', 'impacto', 'equilibrado', 'cinematografico', 'podcast', 'live_jogo'] = 'auto'
    intensity: float = Field(default=0.6, ge=0, le=1)
    variants: bool = False
    options: PlanToggles = Field(default_factory=PlanToggles)


class PlanUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selected: bool = False
    actions: dict[str, bool] = Field(default_factory=dict)


@app.post('/api/projects/{project_id}/clips/{clip_id}/plans')
def create_plans(project_id: str, clip_id: int, request: PlanRequest):
    return service.create_edit_plans(project_id, clip_id, request.model_dump())


@app.get('/api/projects/{project_id}/clips/{clip_id}/plans')
def get_plans(project_id: str, clip_id: int):
    return service.get_edit_plans(project_id, clip_id)


@app.patch('/api/projects/{project_id}/clips/{clip_id}/plans/{name}')
def update_plan(project_id: str, clip_id: int, name: str, changes: PlanUpdate):
    return service.update_edit_plan(project_id, clip_id, name, changes.model_dump())


class FocusRect(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rect: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)


@app.put('/api/projects/{project_id}/clips/{clip_id}/focus')
def clip_focus(project_id: str, clip_id: int, body: FocusRect):
    """Área do "Só o streamer" desenhada à mão (fração do quadro); ``null`` volta para a automática."""
    return service.set_focus_rect(project_id, clip_id, body.rect)


class SubtitleLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: float
    end: float
    text: str = Field(default='', max_length=400)


class SubtitleEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    segments: list[SubtitleLine] = Field(max_length=500)


@app.get('/api/projects/{project_id}/clips/{clip_id}/subtitles')
def clip_subtitles(project_id: str, clip_id: int):
    """Frases da legenda do corte (texto da transcrição)."""
    return service.clip_subtitles(project_id, clip_id)


@app.put('/api/projects/{project_id}/clips/{clip_id}/subtitles')
def set_clip_subtitles(project_id: str, clip_id: int, body: SubtitleEdit):
    """Corrige o texto das frases; as corrigidas não são substituídas pela legenda precisa."""
    return service.set_clip_subtitles(project_id, clip_id, [s.model_dump() for s in body.segments])


@app.delete('/api/projects/{project_id}/clips/{clip_id}/exports')
def delete_export(project_id: str, clip_id: int, format: Literal['9:16', '16:9', '1:1', 'original']):
    """Exclui uma exportação concluída do corte (a cópia na pasta de saída fica)."""
    return service.delete_export(project_id, clip_id, format)


@app.post('/api/projects/{project_id}/clips/{clip_id}/export')
def export_clip(project_id: str, clip_id: int, options: ExportOptions):
    return service.request_export(project_id, clip_id, options.model_dump())


@app.post('/api/projects/{project_id}/clips/{clip_id}/preview')
def preview_clip(project_id: str, clip_id: int, options: ExportOptions):
    """Prévia rápida (meia resolução) da edição final, com as mesmas opções da exportação."""
    return service.request_export(project_id, clip_id, options.model_dump(), preview=True)


@app.get('/api/projects/{project_id}/clips/{clip_id}/preview')
def preview_file(project_id: str, clip_id: int):
    return FileResponse(service.preview_path(project_id, clip_id), media_type='video/mp4',
                        content_disposition_type='inline', headers={'Cache-Control': 'no-cache'})


class ExportAllOptions(ExportOptions):
    formats: list[Literal['9:16', '16:9', '1:1', 'original']] = Field(default=['9:16'], min_length=1, max_length=4)
    clip_ids: Optional[list[int]] = Field(default=None, max_length=500)
    min_score: Optional[int] = Field(default=None, ge=0, le=100)
    shorten_pauses: bool = False


class LocalPath(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=3, max_length=1000)
    options: dict = Field(default_factory=dict)


@app.post('/api/projects/check-path')
def check_local_path(body: LocalPath):
    """Vídeo que já está no computador: tamanho, se a importação vai copiar e se ele já está num projeto."""
    return service.check_path(body.path)


@app.post('/api/projects/from-path')
def create_from_local_path(body: LocalPath):
    """Cria o projeto a partir de um arquivo local, sem enviar pelo navegador (hard link no mesmo disco)."""
    return service.create_from_path(body.path, {k: str(v) for k, v in body.options.items()})


class DuplicateCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    size: int = Field(ge=0)
    quick: str = Field(max_length=100)


@app.post('/api/projects/check-duplicate')
def check_duplicate(body: DuplicateCheck):
    """O vídeo (pela impressão rápida calculada no navegador) já está em algum projeto?"""
    return {'duplicate': service.find_duplicate(body.size, body.quick)}


@app.post('/api/projects/{project_id}/open-folder')
def open_project_folder(project_id: str):
    return service.open_folder(project_id)


@app.post('/api/open-output')
def open_output_folder():
    return service.open_folder(None)


@app.post('/api/projects/{project_id}/export-all')
def export_all(project_id: str, options: ExportAllOptions):
    return service.export_all(project_id, options.model_dump(exclude_none=True))


class AutopilotResume(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acknowledge_rights: bool = False


@app.post('/api/projects/{project_id}/autopilot/resume')
def autopilot_resume(project_id: str, body: AutopilotResume):
    return service.autopilot_resume(project_id, body.acknowledge_rights)


@app.post('/api/projects/{project_id}/autopilot/run')
def autopilot_run(project_id: str):
    return service.autopilot_run(project_id)


@app.get('/api/projects/{project_id}/clips/{clip_id}/publication')
def download_publication(project_id: str, clip_id: int, format: str):
    path, name = service.publication_text(project_id, clip_id, format)
    return FileResponse(path, media_type='text/plain; charset=utf-8', filename=name)


@app.get('/api/settings')
def get_settings():
    return service.get_settings()


@app.put('/api/settings')
def update_settings(changes: dict):
    return service.update_settings(changes)


class ApiKey(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(max_length=200)


@app.put('/api/settings/keys/{name}')
def set_api_key(name: Literal['gemini'], body: ApiKey):
    return service.set_api_key(name, body.key)


@app.delete('/api/settings/keys/{name}')
def delete_api_key(name: Literal['gemini']):
    return service.set_api_key(name, '')


@app.get('/api/projects/{project_id}/clips/{clip_id}/download')
def download_clip(project_id: str, clip_id: int, format: Optional[str] = None):
    path, name = service.export_path(project_id, clip_id, format, 'mp4')
    return FileResponse(path, media_type='video/mp4', filename=name)


@app.get('/api/projects/{project_id}/clips/{clip_id}/subtitles')
def download_subtitles(project_id: str, clip_id: int, format: Optional[str] = None):
    path, name = service.export_path(project_id, clip_id, format, 'srt')
    return FileResponse(path, media_type='application/x-subrip; charset=utf-8', filename=name)


@app.get('/api/projects/{project_id}/report')
def download_report(project_id: str):
    return JSONResponse(service.report(project_id),
                        headers={'Content-Disposition': 'attachment; filename="relatorio_de_revisao.json"'})


# --------------------------------------------------------------------------- tarefas

@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    return service.get_job(job_id)


@app.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: str):
    return service.cancel_job(job_id)
