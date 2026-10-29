"""Configurações persistentes do usuário (data/settings.json).

Guardam o perfil de automação: declarações de direitos padrão, critérios do piloto automático,
formatos de exportação e preferências de narração. Nunca guardam segredos (chaves ficam em
variáveis de ambiente) nem caminhos arbitrários (pastas de entrada/saída só por variável de ambiente).
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time

from . import config, media, rights, storage
from .errors import AppError

_lock = threading.RLock()

STYLES = ('recap', 'comentario', 'documentario')
SUBTITLE_SOURCES = ('fala', 'narracao')
SUBTITLE_STYLES = ('classica', 'dinamica')


def defaults() -> dict:
    return {
        'version': 1,
        'upload_defaults': {
            'target_seconds': 35, 'desired_count': 0,   # 0 = automático
            'source_rights': 'unknown', 'external_audio': 'unknown', 'graphics_rights': 'unknown',
            'third_party_footage': 'unknown', 'monetization': 'unknown', 'license_notes': '',
            'recognize_music_opt_in': False,
        },
        'autopilot': {
            'enabled': False,             # padrão do formulário de envio e da pasta monitorada
            'max_clips': 3,
            'min_score': 55,
            'include_temporal': False,    # cortes temporais (sem análise de conteúdo) só se o usuário quiser
            'formats': ['9:16'],
            'framing': 'blur',
            'subtitles': True,
            'subtitle_style': 'dinamica',
            'hook': True,
            'normalize_audio': True,
            'shorten_pauses': False,
            'narration': {
                'enabled': False, 'style': 'recap', 'voice': config.OPENAI_VOICE, 'original_volume': 60,
                'use_frames': False, 'subtitle_source': 'narracao', 'fallback_without_narration': True,
                'consent': False, 'consent_at': None,
            },
        },
        'curation': {
            'provider': 'heuristica',     # heuristica | ollama (local) | openai | anthropic
            'local_mode': 'rapido',       # Ollama: rapido (só notas e títulos) | completo (com resumo e comentários)
            'all_clips': False,           # automático sem o teto de cortes por hora (todos os aprovados)
            'consent': False, 'consent_at': None,
            'prompt': '',                 # foco opcional ("momentos sobre dinheiro")
        },
        'watch': {'enabled': False, 'autopilot': True},
        'output': {'enabled': True},
        # Exportação: auto = placa de vídeo NVIDIA (NVENC) quando houver, senão processador; x264 = sempre processador.
        'export': {'encoder': 'auto'},
        # Perfis de exportação salvos pelo usuário (os de tipo de conteúdo ficam em BUILTIN_PROFILES).
        'export_profiles': [],
    }


# Perfis prontos por tipo de conteúdo: o usuário aplica no painel do corte ou escolhe ao criar o projeto.
BUILTIN_PROFILES = [
    {'id': 'live_jogo', 'name': 'Live de jogo', 'builtin': True, 'format': '9:16', 'framing': 'split',
     'subtitles': True, 'subtitle_style': 'dinamica', 'refine_subtitles': True, 'normalize_audio': True,
     'hook': True, 'filter': None},
    {'id': 'irl', 'name': 'IRL / rua', 'builtin': True, 'format': '9:16', 'framing': 'streamer',
     'subtitles': True, 'subtitle_style': 'dinamica', 'refine_subtitles': True, 'normalize_audio': True,
     'hook': True, 'filter': None},
    {'id': 'podcast', 'name': 'Podcast / entrevista', 'builtin': True, 'format': '9:16', 'framing': 'track',
     'subtitles': True, 'subtitle_style': 'dinamica', 'refine_subtitles': True, 'normalize_audio': True,
     'hook': True, 'filter': None},
    {'id': 'youtube', 'name': 'YouTube horizontal', 'builtin': True, 'format': '16:9', 'framing': 'fit',
     'subtitles': True, 'subtitle_style': 'classica', 'refine_subtitles': True, 'normalize_audio': True,
     'hook': False, 'filter': None},
]
PROFILE_FIELDS = ('format', 'framing', 'subtitles', 'subtitle_style', 'refine_subtitles', 'normalize_audio', 'hook',
                  'filter')
MAX_PROFILES = 20


def _profiles(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > MAX_PROFILES:
        raise AppError(f'Guarde no máximo {MAX_PROFILES} perfis.', 400, 'bad_settings')
    from .filters import FILTERS
    out, names = [], {p['name'].casefold() for p in BUILTIN_PROFILES}
    for item in raw:
        if not isinstance(item, dict):
            raise AppError('Perfil inválido.', 400, 'bad_settings')
        name = ' '.join(str(item.get('name') or '').split())[:40]
        if not name:
            raise AppError('Dê um nome ao perfil.', 400, 'bad_settings')
        if name.casefold() in names:
            raise AppError(f'Já existe um perfil chamado "{name}".', 400, 'bad_settings')
        names.add(name.casefold())
        profile = {'name': name,
                   'format': _choice(item.get('format'), media.FORMATS, 'Formato do perfil'),
                   'framing': _choice(item.get('framing'), media.FRAMINGS, 'Enquadramento do perfil'),
                   'subtitle_style': _choice(item.get('subtitle_style'), SUBTITLE_STYLES, 'Estilo de legenda'),
                   'filter': item.get('filter') if item.get('filter') in FILTERS and item['filter'] != 'nenhum'
                   else None}
        for key in ('subtitles', 'refine_subtitles', 'normalize_audio', 'hook'):
            profile[key] = _bool(item.get(key, False))
        out.append(profile)
    return out


def _path():
    return storage.data_dir() / 'settings.json'


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        elif key in out:
            out[key] = value
    return out


def load() -> dict:
    with _lock:
        path = _path()
        stored = {}
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                stored = {}
        try:
            return validate(_merge(defaults(), stored if isinstance(stored, dict) else {}))
        except AppError:
            return defaults()


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    raise AppError('Valor inválido nas configurações (esperado verdadeiro/falso).', 400, 'bad_settings')


def _int(value, low: int, high: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise AppError(f'{label} deve ficar entre {low} e {high}.', 400, 'bad_settings')
    return int(value)


def _choice(value, allowed, label: str):
    if value not in allowed:
        raise AppError(f'{label} inválido.', 400, 'bad_settings')
    return value


def validate(s: dict) -> dict:
    up = s['upload_defaults']
    up['target_seconds'] = _int(up['target_seconds'], 10, 90, 'Segundos por corte')
    up['desired_count'] = _int(up['desired_count'], 0, 15, 'Número de cortes (0 = automático)')
    up['source_rights'] = _choice(up['source_rights'], rights.SOURCE_RIGHTS, 'Origem do vídeo')
    up['external_audio'] = _choice(up['external_audio'], rights.YES_NO_UNKNOWN, 'Áudio de terceiros')
    up['graphics_rights'] = _choice(up['graphics_rights'], rights.GRAPHICS, 'Imagens e gráficos')
    up['third_party_footage'] = _choice(up['third_party_footage'], rights.YES_NO_UNKNOWN, 'Trechos de terceiros')
    up['monetization'] = _choice(up['monetization'], rights.YES_NO_UNKNOWN, 'Monetização')
    up['license_notes'] = str(up.get('license_notes') or '')[:2000]
    up['recognize_music_opt_in'] = _bool(up['recognize_music_opt_in'])
    ap = s['autopilot']
    ap['enabled'] = _bool(ap['enabled'])
    ap['max_clips'] = _int(ap['max_clips'], 0, 15, 'Cortes por vídeo (0 = todos os aprovados)')
    ap['min_score'] = _int(ap['min_score'], 0, 100, 'Nota mínima')
    ap['include_temporal'] = _bool(ap['include_temporal'])
    formats = ap['formats'] if isinstance(ap['formats'], list) else []
    formats = [f for f in dict.fromkeys(formats) if f in media.FORMATS]
    if not formats:
        raise AppError('Escolha ao menos um formato para o piloto automático.', 400, 'bad_settings')
    ap['formats'] = formats
    ap['framing'] = _choice(ap['framing'], media.FRAMINGS, 'Enquadramento')
    for key in ('subtitles', 'normalize_audio', 'shorten_pauses', 'hook'):
        ap[key] = _bool(ap[key])
    ap['subtitle_style'] = _choice(ap['subtitle_style'], SUBTITLE_STYLES, 'Estilo de legenda')
    nr = ap['narration']
    for key in ('enabled', 'use_frames', 'fallback_without_narration', 'consent'):
        nr[key] = _bool(nr[key])
    nr['style'] = _choice(nr['style'], STYLES, 'Estilo de narração')
    from .narration import VOICES
    nr['voice'] = _choice(nr['voice'], VOICES, 'Voz')
    nr['original_volume'] = _int(nr['original_volume'], 0, 100, 'Volume do áudio original')
    nr['subtitle_source'] = _choice(nr['subtitle_source'], SUBTITLE_SOURCES, 'Origem das legendas')
    if nr['enabled'] and not nr['consent']:
        raise AppError('Para narrar automaticamente, autorize o envio de transcrições e roteiros à OpenAI.',
                       400, 'consent_required')
    if not nr['consent']:
        nr['consent_at'] = None
    cu = s['curation']
    from .curation import LOCAL_MODES, PROVIDERS, requires_external_consent
    cu['provider'] = _choice(cu['provider'], PROVIDERS, 'Provedor da curadoria')
    cu['local_mode'] = _choice(cu.get('local_mode', 'rapido'), LOCAL_MODES, 'Modo da curadoria local')
    cu['all_clips'] = _bool(cu.get('all_clips', False))
    cu['consent'] = _bool(cu['consent'])
    cu['prompt'] = str(cu.get('prompt') or '')[:300]
    if requires_external_consent(cu['provider']) and not cu['consent']:
        raise AppError('Para usar a curadoria com IA na nuvem, autorize o envio do texto das transcrições ao '
                       'provedor escolhido.', 400, 'consent_required')
    if not requires_external_consent(cu['provider']):
        cu['consent'] = False   # local (heurística, Ollama): nada é enviado, não há consentimento a guardar
    if not cu['consent']:
        cu['consent_at'] = None
    s['watch']['enabled'] = _bool(s['watch']['enabled'])
    s['watch']['autopilot'] = _bool(s['watch']['autopilot'])
    s['output']['enabled'] = _bool(s['output']['enabled'])
    s['export']['encoder'] = _choice(s['export']['encoder'], ('auto', 'x264'), 'Codificador da exportação')
    s['export_profiles'] = _profiles(s['export_profiles'])
    return s


def update(changes: dict) -> dict:
    if not isinstance(changes, dict):
        raise AppError('Configurações inválidas.', 400, 'bad_settings')
    with _lock:
        current = load()
        had_consent = current['autopilot']['narration']['consent']
        had_curation_consent = current['curation']['consent']
        merged = validate(_merge(current, changes))
        nr = merged['autopilot']['narration']
        if nr['consent'] and not had_consent:
            nr['consent_at'] = time.time()
        cu = merged['curation']
        if cu['consent'] and not had_curation_consent:
            cu['consent_at'] = time.time()
        merged['updated_at'] = time.time()
        path = _path()
        fd, tmp = tempfile.mkstemp(prefix='.settings-', suffix='.tmp', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return merged


def public() -> dict:
    s = load()
    s['paths'] = {'watch_dir': str(config.WATCH_DIR), 'output_dir': str(config.OUTPUT_DIR)}
    s['builtin_profiles'] = BUILTIN_PROFILES
    return s
