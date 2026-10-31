"""Referências do projeto para a escolha dos cortes: link da live na Twitch, canal e chat importado; e as linhas
da legenda para a prévia no player.

Separado do ``service.py`` (os nomes continuam disponíveis lá).
"""
from __future__ import annotations

import contextlib
import json
import time

from . import chat, community, config, service, storage, subtitles
from .errors import AppError


def word_lines(project_id: str, start: float, end: float) -> dict:
    """Linhas da legenda animada de um trecho, com o tempo de cada palavra no vídeo inteiro: a prévia no player
    mostra a legenda sem exportar (as mesmas linhas que a exportação usa, antes do plano de edição)."""
    if not (0 <= start < end) or end - start > config.MAX_CLIP_SECONDS:
        raise AppError('Trecho inválido para a prévia da legenda.', 400, 'bad_input')
    p = storage.read_project(project_id)
    lines = subtitles.build_word_lines(p.get('transcript') or [], start, end)
    return {'lines': [[{**w, 'start': round(w['start'] + start, 3), 'end': round(w['end'] + start, 3)} for w in line]
                      for line in lines]}


def set_chat(project_id: str, raw: bytes, name: str = '') -> dict:
    """Importa o chat exportado da live (``app/chat.py``); vale na próxima escolha dos cortes. Vazio remove."""
    folder = storage.project_dir(project_id)
    if not raw:
        with storage.edit_project(project_id) as p:
            p.pop('chat', None)
        with contextlib.suppress(OSError):
            (folder / chat.FILE).unlink()
        return {'chat': None}
    messages = chat.parse(raw)
    with storage.edit_project(project_id) as p:
        storage._write_atomic(folder / chat.FILE, json.dumps(chat.compact(messages)))
        p['chat'] = {'messages': len(messages), 'name': service._clean_text(name, 120), 'imported_at': time.time(),
                     'peaks': None, 'message': f'{len(messages)} mensagens importadas; os picos entram na próxima '
                     'escolha dos cortes.'}
        return {'chat': p['chat']}


def set_reference(project_id: str, twitch_url: str | None = None, channel: str | None = None) -> dict:
    """Referências do projeto para a próxima escolha dos cortes: o link da live na Twitch (vazio remove) e o canal,
    que separa o aprendizado com o 👍/👎 por streamer. ``None`` mantém o valor atual."""
    vod = community.parse_vod(twitch_url) if twitch_url is not None else None
    with storage.edit_project(project_id) as p:
        if twitch_url is not None:
            p['twitch_vod'] = vod
            if not vod:
                p.pop('community', None)
        if channel is not None:
            p['channel'] = service._clean_text(channel, 60) or None
        return {'twitch_vod': p.get('twitch_vod'), 'channel': p.get('channel')}
