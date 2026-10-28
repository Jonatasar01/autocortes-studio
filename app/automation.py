"""Orquestração da central de automação, sem assumir consentimento externo."""
import copy
import hashlib
import json
import uuid
import zipfile
from pathlib import Path
from . import autopilot, config, curation, downloads, narration, service, settings, storage, transcription
from .errors import AppError

PRESETS = {
    'shorts': {'name': 'Shorts automáticos', 'description': '3 melhores cortes verticais, legenda dinâmica e áudio normalizado.',
               'target': 35, 'count': 0, 'max': 3, 'formats': ['9:16'], 'narration': False},
    'multiformato': {'name': 'Distribuição multiformato', 'description': '3 cortes em vertical, horizontal e quadrado.',
                     'target': 45, 'count': 0, 'max': 3, 'formats': ['9:16', '16:9', '1:1'], 'narration': False},
    'narrado': {'name': 'Recap com IA', 'description': 'Seleção, roteiro e voz quando configurados e autorizados. Fallback local explícito.',
                'target': 45, 'count': 0, 'max': 3, 'formats': ['9:16'], 'narration': True},
}


def readiness():
    cfg = settings.load()
    cu = cfg['curation']
    nr = cfg['autopilot']['narration']
    providers = curation.status()
    return [
        {'name': 'Transcrição local', 'ready': transcription.available(),
         'detail': f'Whisper {config.WHISPER_MODEL} (auto: large-v3-turbo na GPU, small na CPU); '
                   'primeiro uso pode baixar o modelo.'},
        {'name': 'Curadoria com IA', 'ready': cu['provider'] != 'heuristica' and cu['consent'] and providers[cu['provider']]['configured'],
         'detail': f"Provedor: {cu['provider']}. Requer chave e consentimento nas Configurações para IA na nuvem."},
        {'name': 'Roteiro e voz', 'ready': nr['enabled'] and nr['consent'] and narration.is_configured(),
         'detail': 'OpenAI: ativação, chave e consentimento necessários. Pode gerar cobrança.'},
        {'name': 'Importação por link', 'ready': downloads.available(), 'detail': 'YouTube e Shorts públicos, até 1080p; sem login.'},
        {'name': 'Piloto automático', 'ready': cfg['autopilot']['enabled'],
         'detail': f"Até {cfg['autopilot']['max_clips']} cortes · {', '.join(cfg['autopilot']['formats'])}"},
    ]


def overview():
    projects = storage.list_projects()
    names = {p['id']: p['name'] for p in projects}
    jobs = []
    for job in service._jobs().recent():
        public = service.public_job(job)
        public.update(project_id=job['project_id'], project_name=names.get(job['project_id'], 'Importação por link'))
        if job['kind'] == 'download' and job['result']:
            public['project_id'] = job['result'].get('project_id')
        jobs.append(public)
    return {'readiness': readiness(), 'presets': PRESETS, 'jobs': jobs,
            'counts': {'projects': len(projects), 'active': sum(j['status'] in ('queued','running','cancelling') for j in jobs),
                       'attention': sum(j['status'] in ('failed','interrupted') for j in jobs),
                       'paused': sum(p.get('autopilot') == 'pausado_direitos' for p in projects)}}


def apply_preset(name):
    if name not in PRESETS:
        raise AppError('Perfil desconhecido.', 400, 'bad_profile')
    preset = PRESETS[name]
    return settings.update({'upload_defaults': {'target_seconds': preset['target'], 'desired_count': preset['count']},
                           'autopilot': {'enabled': True, 'max_clips': preset['max'], 'min_score': 55,
                             'formats': preset['formats'], 'framing': 'blur', 'subtitles': True,
                             'subtitle_style': 'dinamica', 'hook': True, 'normalize_audio': True,
                             'shorten_pauses': not preset['narration'], 'include_temporal': False,
                             'narration': {'enabled': preset['narration']}}})


def import_url(url, form):
    url = downloads.canonical_url(url)
    opts = service.validate_options({k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()})
    if not downloads.available():
        raise AppError('Instale o extra links para importar por URL.', 400, 'download_missing')
    # Só campos validados seguem para a tarefa; consentimentos são snapshots explícitos.
    clean = {k: str(v).lower() if isinstance(v, bool) else v for k, v in opts.items() if v is not None}
    clean['recognize_music_opt_in'] = clean.pop('recognize_music')
    clean['editing_mode'] = str(form.get('editing_mode', 'automatic')).strip().lower()
    params = {'url': url, 'form': clean, 'automation_snapshot': autopilot.snapshot(opts['autopilot']),
              'curation_snapshot': copy.deepcopy(settings.load()['curation'])}
    # O run_id muda a cada snapshot; deduplicação de URL ativa é independente dele.
    key = 'download:' + hashlib.sha256(url.encode()).hexdigest()
    job, created = service._jobs().submit(uuid.uuid4().hex, 'download', params, dedupe_key=key)
    return service.public_job(job) | {'duplicate': not created}


def retry(job_id):
    job = service._jobs().get(job_id)
    if not job or job['status'] not in ('failed', 'interrupted', 'cancelled'):
        raise AppError('Esta tarefa não pode ser repetida.', 409, 'bad_state')
    pid, params = job['project_id'], job['params']
    if job['kind'] == 'download':
        return import_url(params['url'], params['form'])
    if job['kind'] == 'analysis':
        return service.reanalyze(pid)
    if job['kind'] == 'export' and not params.get('narration') and not params.get('preview'):
        # Mesmas opções, mas com os tempos e o título atuais do corte (as edições feitas depois valem).
        options = {k: params[k] for k in RETRY_OPTIONS if k in params}
        if params.get('edit_plan_name'):
            options['edit_plan'] = params['edit_plan_name']
        return service.request_export(pid, params['clip_id'], options)
    raise AppError('Abra o projeto e execute novamente com as opções atuais para preservar suas edições.', 409, 'retry_from_project')


# Opções de uma exportação que voltam na nova tentativa (tempos, título e trechos vêm do corte atual).
RETRY_OPTIONS = ('format', 'framing', 'subtitles', 'subtitle_style', 'refine_subtitles', 'hook', 'normalize_audio',
                 'original_volume', 'subtitle_source', 'shorten_pauses', 'filter')


def bundle(project_id):
    """Empacota somente arquivos registrados; caminho interno nunca vem da requisição."""
    project = storage.read_project(project_id)
    folder = storage.project_dir(project_id)
    if service._jobs().for_project(project_id, active_only=True):
        raise AppError('Aguarde as tarefas para baixar o pacote completo.', 409, 'busy')
    records = [(c['id'], fmt, rec) for c in project.get('clips', []) for fmt, rec in (c.get('exports') or {}).items()]
    if not records:
        raise AppError('Exporte ao menos um corte antes de baixar o pacote.', 409, 'not_exported')
    required = sum((folder/'clips'/name).stat().st_size for _, _, rec in records
                   for name in (rec.get('file'), rec.get('srt_file'), rec.get('package_file'))
                   if name and Path(name).name == name and (folder/'clips'/name).is_file())
    storage.ensure_free_space(required)
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix='bundle-', suffix='.zip', dir=folder)
    import os
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as z:
            included = set()
            for _, _, record in records:
                for key in ('file', 'srt_file', 'package_file'):
                    name = record.get(key)
                    if not name or Path(name).name != name or name in included:
                        continue
                    source = folder/'clips'/name
                    if source.is_file():
                        z.write(source, name)
                        included.add(name)
            z.writestr('revisao.json', json.dumps(service.report(project_id), ensure_ascii=False, indent=2))
        return Path(tmp)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
