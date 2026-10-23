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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.background import BackgroundTask

from . import config, service
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
    return (config.STATIC_DIR / 'studio.html').read_text(encoding='utf-8')


@app.get('/classic', response_class=HTMLResponse)
def classic_editor():
    return (config.STATIC_DIR / 'index.html').read_text(encoding='utf-8')


@app.get('/static/{name}')
def static(name: str):
    if name not in {'styles.css', 'app.js', 'favicon.svg', 'studio.css', 'studio.js'}:
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    return FileResponse(config.STATIC_DIR / name)


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


@app.get('/api/projects/{project_id}')
def get_project(project_id: str):
    return service.get_project(project_id)


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str):
    service.delete_project(project_id)
    return {'ok': True}


@app.post('/api/projects/{project_id}/reanalyze')
def reanalyze(project_id: str):
    return service.reanalyze(project_id)


@app.get('/api/projects/{project_id}/source')
def source_file(project_id: str):
    path, name = service.source_path(project_id)
    return FileResponse(path, media_type='video/mp4' if path.suffix in ('.mp4', '.m4v') else None,
                        filename=name, content_disposition_type='inline')


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


@app.patch('/api/projects/{project_id}/clips/{clip_id}')
def edit_clip(project_id: str, clip_id: int, edit: ClipEdit):
    return service.edit_clip(project_id, clip_id, edit.start, edit.end, edit.title)


class ExportOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    format: Optional[Literal['9:16', '16:9', '1:1', 'original']] = None
    portrait: bool = True  # compatibilidade: usado quando "format" não é enviado
    framing: Literal['fit', 'blur', 'track'] = 'fit'
    subtitles: bool = True
    subtitle_style: Literal['classica', 'dinamica'] = 'classica'
    hook: bool = False
    normalize_audio: bool = False
    acknowledge_rights: bool = False
    narration: bool = False
    narration_consent: bool = False
    voice: Optional[str] = Field(default=None, max_length=20)
    original_volume: int = Field(default=100, ge=0, le=100)
    subtitle_source: Optional[Literal['fala', 'narracao']] = None
    shorten_pauses: bool = False


@app.post('/api/projects/{project_id}/clips/{clip_id}/export')
def export_clip(project_id: str, clip_id: int, options: ExportOptions):
    return service.request_export(project_id, clip_id, options.model_dump())


class NarrationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    style: Literal['recap', 'comentario', 'documentario'] = 'recap'
    context: str = Field(default='', max_length=500)
    use_frames: bool = False
    consent: bool = False


@app.post('/api/projects/{project_id}/clips/{clip_id}/narration')
def request_narration(project_id: str, clip_id: int, options: NarrationRequest):
    return service.request_narration(project_id, clip_id, options.model_dump())


class NarrationSegment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=600)


class NarrationEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    segments: list[NarrationSegment] = Field(min_length=1, max_length=60)


@app.put('/api/projects/{project_id}/clips/{clip_id}/narration')
def update_narration(project_id: str, clip_id: int, edit: NarrationEdit):
    return service.update_narration(project_id, clip_id, [s.model_dump() for s in edit.segments])


class ExportAllOptions(ExportOptions):
    formats: list[Literal['9:16', '16:9', '1:1', 'original']] = Field(default=['9:16'], min_length=1, max_length=4)
    clip_ids: Optional[list[int]] = Field(default=None, max_length=50)
    min_score: Optional[int] = Field(default=None, ge=0, le=100)
    shorten_pauses: bool = False


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
