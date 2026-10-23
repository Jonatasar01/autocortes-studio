"""FilmCraft opcional pela fila; FFmpeg continua sendo o exportador principal."""
from . import service, storage
from .errors import AppError, Cancelled
from .integrations.filmcraft import FilmCraftBridge, BridgeError


def request(project_id, acknowledge=False):
    p = storage.read_project(project_id)
    if p.get('status') != 'ready':
        raise AppError('Aguarde a análise do vídeo.', 409, 'not_ready')
    if service._jobs().for_project(project_id, active_only=True):
        raise AppError('Aguarde as tarefas antes de criar a montagem editável.', 409, 'busy')
    if (p.get('rights') or {}).get('requires_acknowledgement') and not acknowledge:
        raise AppError('Leia e confirme os alertas de direitos antes de exportar.', 409, 'rights_ack_required')
    bridge = FilmCraftBridge()
    if not bridge.binary or not bridge.binary.is_file():
        raise AppError('FilmCraft não instalado. Configure FILMCRAFT_CLI para usar a montagem editável.',
                       400, 'filmcraft_missing')
    manifest = bridge.decision_from_project(project_id)
    if acknowledge:
        import time
        with storage.edit_project(project_id) as current:
            current.setdefault('rights_acknowledgements', []).append({'at': time.time(), 'source': 'FilmCraft'})
    job, created = service._jobs().submit(project_id, 'filmcraft', {'manifest': manifest},
                                         dedupe_key='filmcraft:'+project_id)
    return service.public_job(job) | {'duplicate': not created}


def run(ctx):
    ctx.stage('filmcraft', 'Criando montagem editável e render com FilmCraft')
    ctx.progress(0.1)
    target = storage.project_dir(ctx.project_id)/'advanced'/ctx.id
    try:
        FilmCraftBridge(cancel_event=ctx.cancel_event, timeout=1800).build(ctx.params['manifest'], target)
    except BridgeError as exc:
        if ctx.cancel_event.is_set():
            raise Cancelled() from None
        raise AppError(str(exc), 422, 'filmcraft_failed') from None
    return {'project_file': 'timeline.fcproj', 'render_file': 'render.mp4', 'subtitle_file': 'captions.srt'}


def artifact(project_id, job_id, kind):
    if not storage.valid_id(job_id):
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    job = service._jobs().get(job_id)
    if not job or job['project_id'] != project_id or job['kind'] != 'filmcraft' or job['status'] != 'done':
        raise AppError('A montagem ainda não está disponível.', 404, 'not_found')
    names = {'project': 'timeline.fcproj', 'render': 'render.mp4', 'subtitles': 'captions.srt'}
    if kind not in names:
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    file = storage.project_dir(project_id)/'advanced'/job_id/names[kind]
    if not file.is_file():
        raise AppError('Arquivo não encontrado.', 404, 'not_found')
    return file
