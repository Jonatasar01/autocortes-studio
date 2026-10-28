"""Página inicial: projetos com miniatura, tarefas em andamento, últimos cortes exportados e dependências.

Tudo sai de dados que já existem (fila SQLite, project.json, saúde do FFmpeg e da transcrição): esta camada só
monta a visão de "acesso rápido", sem ler o vídeo nem a transcrição dos projetos.
"""
from __future__ import annotations

from . import config, downloads, media, reframe, service, storage, transcription

KIND_LABELS = {'analysis': 'Análise', 'export': 'Exportação', 'narration': 'Narração',
               'download': 'Importação por link', 'filmcraft': 'Edição avançada'}
ACTIVE = ('queued', 'running', 'cancelling')


def _names() -> dict[str, str]:
    return {item['id']: item['name'] for item in storage.list_projects()}


def _job_view(job: dict, names: dict[str, str]) -> dict:
    view = service.public_job(job)
    view.update(project_id=job['project_id'], project_name=names.get(job['project_id'], 'Importação por link'),
                kind_label=KIND_LABELS.get(job['kind'], job['kind']),
                clip_title=(job.get('params') or {}).get('title'))
    return view


def jobs(limit: int = 30) -> list[dict]:
    """Tarefas de todos os projetos: as ativas primeiro, depois as mais recentes (prévias ficam de fora)."""
    names = _names()
    rows = [j for j in service._jobs().recent(min(200, limit * 3))
            if not (j.get('params') or {}).get('preview')]
    return [_job_view(j, names) for j in rows[:limit]]


def recent_exports(limit: int = 8) -> list[dict]:
    """Últimos cortes exportados que ainda existem no disco (um por corte e formato)."""
    names, seen, out = _names(), set(), []
    for job in service._jobs().recent(200):
        prm, result = job.get('params') or {}, job.get('result') or {}
        if job['kind'] != 'export' or job['status'] != 'done' or prm.get('preview') or not result.get('file'):
            continue
        key = (job['project_id'], prm.get('clip_id'), prm.get('format'))
        if key in seen or job['project_id'] not in names:
            continue
        seen.add(key)
        try:
            if not (storage.project_dir(job['project_id']) / 'clips' / result['file']).is_file():
                continue   # substituído por uma exportação mais nova ou apagado
        except Exception:
            continue
        out.append({'project_id': job['project_id'], 'project_name': names[job['project_id']],
                    'clip_id': prm.get('clip_id'), 'title': prm.get('title'), 'format': prm.get('format'),
                    'duration': round(float(prm.get('end', 0)) - float(prm.get('start', 0)), 1),
                    'finished': job.get('finished')})
        if len(out) >= limit:
            break
    return out


def dependencies() -> list[dict]:
    """Itens essenciais com estado e, quando falta algo, o que fazer."""
    caps = media.ffmpeg_capabilities()
    gpu = transcription.refine_available()
    has_whisper = transcription.available()
    free = storage.free_bytes() / 1024 ** 3
    items = [
        {'id': 'ffmpeg', 'label': 'FFmpeg (vídeo e áudio)', 'ok': bool(caps['ffmpeg'] and caps['ffprobe']),
         'detail': 'Instalado' if caps['ffmpeg'] else 'Não encontrado',
         'fix': 'Instale o FFmpeg e adicione a pasta "bin" ao PATH do Windows; depois reabra o AutoCortes.'},
        {'id': 'legendas', 'label': 'Legendas no vídeo', 'ok': bool(caps.get('libass')),
         'detail': 'Disponíveis' if caps.get('libass') else 'FFmpeg sem libass',
         'fix': 'Use uma versão "full" do FFmpeg (ex.: gyan.dev), que já inclui a libass.'},
        {'id': 'transcricao', 'label': 'Reconhecimento de fala', 'ok': has_whisper,
         'detail': (('Placa de vídeo (GPU) · ' if gpu else 'Processador (CPU) · ') + transcription.model_name(
             'cuda' if gpu else 'cpu')) if has_whisper else 'Não instalado',
         'fix': 'Rode: pip install -r requirements.txt (sem isso os cortes são só por tempo).'},
        {'id': 'rostos', 'label': 'Seguir rosto e webcam', 'ok': reframe.available(),
         'detail': 'Disponível' if reframe.available() else 'Não instalado',
         'fix': 'Rode: pip install -r requirements-ai.txt'},
        {'id': 'codificacao', 'label': 'Exportação', 'ok': True,
         'detail': ('Placa de vídeo (NVENC): cerca de 2× mais rápida' if media.choose_encoder() == 'nvenc'
                    else 'Processador (x264)'),
         'fix': 'Para exportar pela placa: NVIDIA com driver atualizado e a opção ligada em Configurações.'},
        {'id': 'disco', 'label': 'Espaço livre', 'ok': free >= 20, 'detail': f'{free:.0f} GB',
         'fix': 'Libere espaço: cada live importada ocupa o tamanho do arquivo original.'},
    ]
    if not gpu and has_whisper:
        items[2]['hint'] = 'Com uma placa NVIDIA e requirements-gpu.txt a análise fica várias vezes mais rápida.'
    return items


def overview() -> dict:
    projects = service.list_projects()
    return {
        'projects': projects,
        'active_jobs': [j for j in jobs(60) if j['status'] in ACTIVE],
        'recent_exports': recent_exports(),
        'dependencies': dependencies(),
        'links_enabled': downloads.available(),
        'limits': {'max_upload_gb': round(config.MAX_UPLOAD_BYTES / 1024 ** 3, 1),
                   'max_source_hours': config.MAX_SOURCE_SECONDS // 3600},
        'output_dir': str(config.OUTPUT_DIR),
    }
