"""Título, descrição e hashtags para publicar um corte, escritos pela IA a partir do que foi falado nele.

Usa a mesma IA da escolha dos cortes (Configurações → Escolha dos cortes com IA): o Ollama roda neste computador; o
Gemini só com o consentimento já dado lá, e o envio fica registrado no projeto como os da curadoria. Sem IA
disponível, devolve a sugestão local de sempre (título do corte, resumo da fala e hashtags pelas palavras).
"""
from __future__ import annotations

import re
import time

from . import curation, publish, settings, storage
from .errors import AppError

MAX_TEXT = 2500
SYSTEM = ('Você escreve os textos de publicação de cortes de lives brasileiras para YouTube Shorts e TikTok. '
          'Use só o que foi dito no trecho: não invente fatos, nomes ou resultados. '
          'Título: até 70 caracteres, curioso e direto, no máximo um emoji, sem caixa alta inteira e sem hashtags. '
          'Descrição: 1 ou 2 frases curtas que dão contexto sem entregar o final. '
          'Hashtags: 3 a 5, em minúsculas, sem acento, sobre o assunto do trecho (não use #shorts nem #fyp). '
          'Responda só com o JSON pedido.')
SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['title', 'description', 'hashtags'],
          'properties': {'title': {'type': 'string'}, 'description': {'type': 'string'},
                         'hashtags': {'type': 'array', 'items': {'type': 'string'}}}}


def clip_text(p: dict, clip: dict) -> str:
    """O que foi falado no corte (transcrição do trecho), limitado para caber no pedido."""
    parts = [s['text'].strip() for s in p.get('transcript') or []
             if s['end'] > clip['start'] and s['start'] < clip['end'] and s.get('text')]
    text = re.sub(r'\s+', ' ', ' '.join(parts)).strip() or clip.get('description') or ''
    return text[:MAX_TEXT]


def _tag(value: str) -> str | None:
    tag = re.sub(r'[^0-9a-z_]', '', publish._fold(str(value).lower().lstrip('#')))
    return '#' + tag if 2 <= len(tag) <= 30 and tag not in ('shorts', 'fyp') else None


def _fallback(clip: dict, text: str, fmt: str) -> dict:
    return {'title': (clip.get('title') or 'Corte')[:100], 'description': publish._summary(text),
            'hashtags': publish.hashtags(text, fmt), 'provider': None}


def suggest(project_id: str, clip_id: int, fmt: str = '9:16') -> dict:
    p = storage.read_project(project_id)
    clip = next((c for c in p.get('clips') or [] if c['id'] == clip_id), None)
    if clip is None:
        raise AppError('Corte não encontrado.', 404, 'not_found')
    text = clip_text(p, clip)
    if len(text.split()) < 5:
        return _fallback(clip, text, fmt)
    cfg = settings.load()['curation']
    provider = cfg.get('provider') if cfg.get('provider') in ('ollama', 'gemini') else 'ollama'
    if provider == 'gemini' and not (cfg.get('consent') and curation.is_configured('gemini')):
        provider = 'ollama'
    if not curation.is_configured(provider):
        return _fallback(clip, text, fmt)
    hint = ''
    if (clip.get('community') or {}).get('title'):
        hint = f"\nTítulo que o público deu ao clipe na Twitch: {clip['community']['title']}"
    channel = p.get('channel') or ((p.get('community') or {}).get('video') or {}).get('channel')
    user = (f"Canal: {channel or 'não informado'}{hint}\nTrecho falado no corte:\n\"\"\"{text}\"\"\"")
    try:
        data = curation.ask(provider, SYSTEM, user, SCHEMA, 'textos_publicacao', max_tokens=800)
    except AppError as exc:
        out = _fallback(clip, text, fmt)
        out['warning'] = f'A IA não respondeu ({exc.message}); ficou a sugestão sem IA.'
        return out
    if curation.requires_external_consent(provider):
        with storage.edit_project(project_id) as proj:
            proj.setdefault('cloud_consents', []).append({
                'granted_at': cfg.get('consent_at'), 'used_at': time.time(),
                'provider': curation.PROVIDER_LABELS[provider], 'model': curation.model_for(provider),
                'scope': 'título e descrição de um corte (somente texto da transcrição)', 'source': 'Configurações'})
    title = re.sub(r'\s+', ' ', str(data.get('title') or '')).strip().strip('"')[:100]
    description = re.sub(r'[ \t]+', ' ', str(data.get('description') or '')).strip()[:1000]
    tags = [t for t in (_tag(x) for x in data.get('hashtags') or [] if isinstance(x, str)) if t][:5]
    if fmt == '9:16':
        tags.append('#shorts')
    if not title:
        return _fallback(clip, text, fmt)
    return {'title': title, 'description': description, 'hashtags': list(dict.fromkeys(tags)),
            'provider': curation.PROVIDER_LABELS[provider]}
