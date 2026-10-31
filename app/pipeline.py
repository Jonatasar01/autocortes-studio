"""Etapas de processamento executadas pela fila: análise do vídeo e exportação de cortes.

Nenhum projeto é marcado como ``ready`` e nenhuma exportação é registrada antes de os
resultados serem validados.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import shutil
import threading
import time

from . import (audio_events, community, config, curation, direction, filters, media, preference, publish, reactions,
               reframe, rights, selection, settings, storage, subtitles, transcription)
from .errors import AppError, Cancelled
from .jobs import JobContext

log = logging.getLogger('autocortes.pipeline')


# --------------------------------------------------------------------------- análise

class _AnyEvent:
    """Evento de cancelamento que dispara com o do job ou com um próprio (``set``)."""

    def __init__(self, parent):
        self.parent, self.own = parent, threading.Event()

    def set(self) -> None:
        self.own.set()

    def is_set(self) -> bool:
        return self.own.is_set() or bool(self.parent is not None and self.parent.is_set())

    def wait(self, timeout: float) -> bool:
        return self.own.wait(timeout) or self.is_set()


def _saved_analysis(pid: str) -> tuple[dict, list[dict], dict, list[float]]:
    """Mídia, transcrição e cenas já gravadas: refazer a seleção não transcreve de novo (horas numa live)."""
    p = storage.read_project(pid)
    t_status = p.get('transcription') or {}
    if not p.get('media') or t_status.get('state') != 'ok' or not p.get('transcript'):
        raise AppError('Este projeto não tem transcrição salva; use Reanalisar.', 409, 'no_transcript')
    return p['media'], p['transcript'], t_status, p.get('scenes') or []


def _audio_layer(ctx: JobContext, folder: Path, info: dict, transcript: list[dict], *, original: Path,
                 wav: Path | None = None):
    """Camada de áudio da seleção (``app/audio_events.py``): ``AudioIndex`` ou ``None``.

    Opcional e nunca fatal: sem áudio, desligada ou com falha, a seleção segue só com o texto.
    """
    if not config.AUDIO_LAYER or not transcript or info.get('audio_index') is None:
        return None
    ctx.stage('audio_eventos', 'Analisando o áudio (volume e reações)')
    started = time.time()
    try:
        source = wav if wav is not None else original
        source_info = {**info, 'audio_index': 0} if wav is not None else info
        track = audio_events.energy_track(source, source_info, folder / 'cache', stamp_of=original,
                                          cancel_event=ctx.cancel_event)
        result = audio_events.analyze(track, transcript)
    except Cancelled:
        raise
    except Exception as exc:  # a seleção por texto continua valendo
        log.warning('Camada de áudio indisponível: %s', exc)
        ctx.warn('Análise de áudio não concluída; a seleção usou só a transcrição.')
        return None
    counts: dict[str, int] = {}
    for e in result['events']:
        counts[e['tipo']] = counts.get(e['tipo'], 0) + 1
    with storage.edit_project(ctx.project_id) as p:
        p['audio_analysis'] = {'state': 'ok', 'version': audio_events.VERSION, 'events': counts,
                               'seconds': round(time.time() - started, 1)}
    return result['index']


def _community_layer(ctx: JobContext, folder: Path, source: Path, info: dict, transcript: list[dict],
                     scenes: list[float], target: int, clips: list[dict]) -> list[dict]:
    """Clipes do público na Twitch (``app/community.py``) na frente da lista, quando o projeto tem o link da live.

    Opcional e nunca fatal: sem credenciais, sem internet ou sem nenhum clipe encaixado, os cortes ficam como estão.
    """
    p = storage.read_project(ctx.project_id)
    vod = p.get('twitch_vod')
    if not vod:
        return clips
    ctx.stage('clipes_publico', 'Buscando os clipes que o público fez na Twitch')
    old = p.get('community') or {}
    previous = {c['id']: c for c in old.get('clips') or []} if old.get('video_id') == vod else {}
    try:
        video, found = community.fetch_clips(vod)
        track = None
        if info.get('audio_index') is not None and community.aligner_available():
            ctx.stage('clipes_publico', 'Encaixando os clipes do público no vídeo pelo áudio')
            track = audio_events.energy_track(source, info, folder / 'cache', cancel_event=ctx.cancel_event)
        aligned = community.align(found, video, info['duration'], track, previous=previous,
                                  cancel_event=ctx.cancel_event)
    except Cancelled:
        raise
    except (AppError, media.FFmpegError) as exc:
        message = exc.message if isinstance(exc, AppError) else 'falha ao ler o áudio do vídeo'
        with storage.edit_project(ctx.project_id) as p:
            p['community'] = {**old, 'state': 'failed', 'video_id': vod,
                              'message': f'Clipes da Twitch não usados: {message}'}
        ctx.warn(f'Clipes da Twitch não usados: {message}')
        return clips
    found_moments = community.moments(aligned)
    matched = sum(1 for c in aligned if c['start'] is not None)
    if not found:
        message = 'Nenhum clipe do público nesta live.'
    elif not matched:
        message = (f'{len(found)} clipe(s) na Twitch, mas nenhum foi encontrado neste vídeo'
                   + ('' if community.aligner_available() else ' (instale o yt-dlp para encaixar pelo áudio)') + '.')
    else:
        message = f'{matched} de {len(found)} clipe(s) encontrados no vídeo, em {len(found_moments)} momento(s).'
    with storage.edit_project(ctx.project_id) as p:
        p['community'] = {'state': 'ok', 'video_id': vod, 'video': video, 'found': len(found), 'matched': matched,
                          'moments': len(found_moments), 'message': message, 'clips': aligned}
    if not found_moments:
        return clips
    return community.apply(clips, found_moments, transcript, info['duration'], scenes, target)


def run_analysis(ctx: JobContext) -> dict:
    pid = ctx.project_id
    folder = storage.project_dir(pid)
    with storage.edit_project(pid) as p:
        p['status'] = 'analyzing'
        p['error'] = None
        p['analysis_job'] = ctx.id
        source = folder / p['filename']
        opts = {k: p.get(k) for k in ('source_rights', 'external_audio', 'license_notes', 'graphics_rights',
                                      'third_party_footage', 'monetization', 'target_seconds', 'desired_count')}
        # Projeto já pronto: a transcrição nova só substitui a atual (com as correções do usuário) no fim.
        keep_early = not p.get('clips')

    saved = _saved_analysis(pid) if ctx.params.get('reselect') else None
    audio_index = None
    if saved:  # refazer só a seleção: mídia, transcrição e cenas da análise anterior
        info, transcript, t_status, scenes = saved
        ctx.progress(0.88)
    else:
        ctx.stage('validando', 'Validando o arquivo de vídeo')
        info = media.probe_media(source)
        warnings = media.integrity_warnings(source, info)
        ctx.progress(0.03)
        if media.needs_browser_remux(source, info):
            ctx.stage('validando', 'Convertendo para MP4 sem perda (para o vídeo tocar no navegador)')
            if media.remux_for_browser(source, info, cancel_event=ctx.cancel_event,
                                       on_progress=lambda f, _m='': ctx.progress(0.03 + 0.04 * f)):
                info = media.probe_media(source)
            else:
                warnings.append('O vídeo não pôde ser convertido para MP4 e pode não tocar no navegador; '
                                'a edição e a exportação continuam funcionando.')

        review = rights.rights_review(opts['source_rights'], opts['external_audio'], opts['license_notes'] or '',
                                      info['audio'], opts.get('graphics_rights') or 'unknown',
                                      opts.get('third_party_footage') or 'unknown', opts.get('monetization') or 'unknown')
        with storage.edit_project(pid) as p:
            p.update({'media': info, 'rights': review, 'warnings': warnings})
        ctx.progress(0.08)

        # Cenas em paralelo com a transcrição: na GPU a detecção quase não usa CPU, e em vídeos longos ela
        # levava quase tanto tempo quanto a transcrição. Pasta própria: a transcrição apaga a sua ao terminar.
        scene_state: dict = {'fraction': 0.0, 'scenes': [], 'error': None}
        scene_stop = _AnyEvent(ctx.cancel_event)   # para a detecção se a análise falhar antes dela terminar

        def find_scenes() -> None:
            try:
                scene_state['scenes'] = media.detect_scenes(
                    source, info, folder / 'work_cenas', cancel_event=scene_stop,
                    on_progress=lambda f, _m='': scene_state.__setitem__('fraction', f))
            except Cancelled as exc:  # relançado na thread do job
                scene_state['error'] = exc
            except Exception:
                # As cenas só refinam as bordas dos cortes: uma falha aqui não pode descartar horas de
                # transcrição. Segue sem elas.
                log.warning('Detecção de cenas falhou; seguindo sem cenas.', exc_info=True)

        scene_thread = threading.Thread(target=find_scenes, name=f'cenas-{pid[:8]}', daemon=True)
        scene_thread.start()

        transcript: list[dict] = []
        if not info['audio']:
            t_status = {'state': 'no_audio', 'message': 'Vídeo sem áudio: não há fala para analisar; cortes temporais.'}
        elif not transcription.available():
            t_status = {'state': 'not_installed', 'message': 'Transcrição não instalada (faster-whisper). Sem '
                        'transcrição, os cortes são apenas temporais — não há análise do conteúdo falado.'}
        else:
            work = folder / 'work'
            try:
                storage.ensure_free_space(int(info['duration'] * 32_000) + 50 * 1024 ** 2, folder)
                ctx.stage('extraindo_audio', 'Extraindo áudio para transcrição')
                wav = media.extract_audio_wav(source, info, work / 'audio.wav', on_progress=ctx.span(0.08, 0.14),
                                              cancel_event=ctx.cancel_event)
                ctx.stage('transcrevendo', 'Transcrevendo a fala localmente')
                transcript, t_status = transcription.transcribe(wav, info['duration'], on_progress=ctx.span(0.14, 0.74),
                                                                cancel_event=ctx.cancel_event)
                if keep_early and t_status['state'] == 'ok':
                    # Gravada já: numa live, a curadoria depois leva quase tanto quanto a transcrição. Se a análise
                    # for cancelada ou falhar daqui em diante, "Refazer seleção" continua sem transcrever de novo.
                    with storage.edit_project(pid) as p:
                        p.update({'transcript': transcript, 'transcription': t_status,
                                  'transcript_status': t_status['message']})
                # Energia do áudio a partir do WAV já extraído (sem decodificar o vídeo de novo).
                audio_index = _audio_layer(ctx, folder, info, transcript, wav=wav, original=source)
            except media.FFmpegError as exc:
                t_status = {'state': 'failed', 'message': 'Não foi possível extrair o áudio; cortes temporais. '
                            + exc.args[0]}
            except BaseException:
                scene_stop.set()
                raise
            finally:
                shutil.rmtree(work, ignore_errors=True)
        ctx.progress(0.75)

        if scene_thread.is_alive():
            ctx.stage('cenas', 'Detectando mudanças de cena')
            while scene_thread.is_alive():
                scene_thread.join(1.0)
                ctx.progress(0.75 + 0.13 * scene_state['fraction'])
        scene_thread.join()
        shutil.rmtree(folder / 'work_cenas', ignore_errors=True)
        if scene_state['error'] is not None:
            raise scene_state['error']
        scenes = scene_state['scenes']
        if keep_early:
            with storage.edit_project(pid) as p:
                p['scenes'] = scenes
        ctx.progress(0.88)

    if audio_index is None and saved:
        audio_index = _audio_layer(ctx, folder, info, transcript, original=source)
    ctx.stage('selecionando', 'Selecionando os melhores trechos')
    fallback = None if t_status['state'] == 'ok' else t_status['message']
    live_cfg = settings.load()['curation']
    cur_cfg = dict(storage.read_project(pid).get('curation_profile') or live_cfg)
    unlimited = bool(cur_cfg.get('all_clips'))   # "sem limite": todos os aprovados, sem teto por hora
    try:   # o 👍/👎 dos projetos (deste canal, quando há avaliações suficientes) ajusta a nota dos trechos
        liked = preference.build(preference.channel_of(storage.read_project(pid)))
    except Exception:
        log.warning('Aprendizado com as avaliações indisponível.', exc_info=True)
        liked = None
    clips, sel = selection.select_clips(transcript, info['duration'], opts['target_seconds'], opts['desired_count'],
                                        scenes, fallback_reason=fallback, audio=audio_index, unlimited=unlimited,
                                        preference=liked)
    ctx.progress(0.9)
    provider = cur_cfg['provider'] if cur_cfg.get('provider') in curation.PROVIDERS else 'heuristica'
    remote = curation.requires_external_consent(provider)
    # Um perfil congelado não pode restaurar consentimento posteriormente revogado (só vale para a nuvem).
    if remote and (not live_cfg['consent'] or live_cfg['provider'] != cur_cfg['provider']):
        cur_cfg['consent'] = False
    curation_status = {'state': 'disabled', 'provider': 'heuristica',
                       'message': 'Cortes escolhidos sem IA (desligada em Configurações).'}
    if provider != 'heuristica' and sel['mode'] != 'transcricao':
        curation_status = {'state': 'skipped', 'provider': provider,
                           'message': 'IA não usada: não há fala transcrita para ela avaliar.'}
    elif remote and not cur_cfg['consent']:
        curation_status = {'state': 'skipped', 'provider': provider,
                           'message': 'IA não usada: falta autorizar o envio do texto em Escolha dos cortes com IA.'}
    elif provider != 'heuristica':
        ctx.stage('curadoria_ia', f'Escolhendo e avaliando trechos com IA ({curation.PROVIDER_LABELS[provider]})')
        if remote:   # o Ollama roda neste computador: nada é enviado, nada a registrar
            with storage.edit_project(pid) as p:
                p.setdefault('cloud_consents', []).append({
                    'granted_at': cur_cfg['consent_at'], 'used_at': time.time(),
                    'provider': curation.PROVIDER_LABELS[provider], 'model': curation.model_for(provider),
                    'scope': 'curadoria de cortes (somente texto da transcrição)', 'source': 'Configurações'})
        try:
            ai_clips, curation_status = curation.curate(
                provider, transcript, info['duration'], opts['target_seconds'], opts['desired_count'], scenes,
                prompt=cur_cfg.get('prompt') or '', unlimited=unlimited,
                cancel_event=ctx.cancel_event, on_progress=ctx.span(0.9, 0.99))
            # Fusão: um bom candidato da heurística não some só porque a IA trouxe outra lista.
            clips = selection.merge_candidates(ai_clips, clips, limit=sel.get('limit'))
            # A lista inteira da IA fica guardada: a curadoria local leva muito tempo e não deve se perder no limite.
            with storage.edit_project(pid) as p:
                p['curation_clips'] = [{k: c.get(k) for k in ('start', 'end', 'score', 'title', 'ai_score',
                                                              'heuristic_score', 'hook_text', 'reasons')}
                                       for c in ai_clips]
        except Cancelled:
            raise
        except AppError as exc:
            curation_status = {'state': 'failed', 'provider': provider,
                               'message': f'A escolha com IA falhou ({exc.message}); os cortes foram escolhidos sem IA.'}
            ctx.warn(curation_status['message'])
    if curation_status['state'] == 'ok':
        ranker = {'state': 'skipped', 'message': 'Ordem dos cortes definida pela IA.'}
    elif provider == 'ollama' and curation_status['state'] == 'failed':
        # O refinador usaria o mesmo Ollama que acabou de falhar: não repete, fica a seleção heurística.
        ranker = {'state': 'skipped', 'message': 'A IA neste computador não terminou; os cortes foram escolhidos '
                  'sem IA, sem tentar de novo.'}
    else:
        ctx.stage('ia_local', 'Refinando com IA local (se disponível)')
        ranker = selection.refine_with_ollama(clips, cancel_event=ctx.cancel_event, on_progress=ctx.span(0.9, 0.99))

    clips = _community_layer(ctx, folder, source, info, transcript, scenes, opts['target_seconds'], clips)
    valid = [c for c in clips if 0 <= c['start'] < c['end'] <= info['duration'] + 0.05 and c['end'] - c['start'] >= 1]
    if not valid:
        raise AppError('Nenhum corte válido pôde ser gerado para este vídeo.', 422, 'no_clips')
    with storage.edit_project(pid) as p:
        p.update({'transcript': transcript, 'transcription': t_status, 'transcript_status': t_status['message'],
                  'scenes': scenes, 'selection': sel, 'ranker': ranker, 'ranker_status': ranker['message'],
                  'curation': curation_status,
                  'clips': valid, 'status': 'ready', 'ready_at': time.time()})
    ctx.progress(1.0, 'Análise concluída')
    return {'clips': len(valid), 'mode': sel['mode']}


def end_analysis(ctx: JobContext, status: str, error: str | None) -> None:
    if status == 'done':
        return
    with contextlib.suppress(AppError):
        with storage.edit_project(ctx.project_id) as p:
            if p.get('analysis_job') not in (None, ctx.id):
                return
            if p.get('clips') and (ctx.params.get('reselect') or status in ('failed', 'cancelled')):
                # Refazer a seleção ou a análise de um projeto pronto não terminou: os cortes anteriores continuam
                # valendo. (Interrompida pelo fechamento do app, a análise é retomada sozinha ao reabrir.)
                p['status'] = 'ready'
                return
            p['status'] = {'cancelled': 'cancelled', 'interrupted': 'interrupted'}.get(status, 'error')
            p['error'] = error or {'cancelled': 'Análise cancelada pelo usuário.'}.get(status, 'A análise não terminou.')


# --------------------------------------------------------------------------- exportação

def run_export(ctx: JobContext) -> dict:
    pid, prm = ctx.project_id, ctx.params
    folder = storage.project_dir(pid)
    p = storage.read_project(pid)
    if p.get('status') != 'ready':
        raise AppError('O projeto não está pronto para exportação.', 409, 'not_ready')
    info = p['media']
    if prm.get('caption_cover'):   # legenda já gravada no vídeo: a área marcada é apagada antes do enquadramento
        info = {**info, 'caption_cover': prm['caption_cover']}
    start, end = float(prm['start']), min(float(prm['end']), info['duration'])
    ctx.stage('preparando', 'Preparando exportação')
    # Estimativa conservadora: ~12 Mbit/s + arquivo temporário
    storage.ensure_free_space(int((end - start) * 1.5 * 1024 ** 2 * 2) + 100 * 1024 ** 2, folder)
    ass_text = srt_text = None
    warnings: list[str] = []
    keep = None
    if prm.get('shorten_pauses'):
        ctx.stage('pausas', 'Encurtando pausas longas')
        keep = (media.pause_intervals(p.get('transcript') or [], start, end) or
                media.silence_intervals(folder / p['filename'], info, start, end - start, cancel_event=ctx.cancel_event))
        if keep:
            keep = media.snap_intervals(keep, media.output_fps(info) or info.get('fps') or 30)
        else:
            warnings.append('Nenhuma pausa longa encontrada neste trecho; exportado sem encurtar.')
    plan = prm.get('edit_plan')
    if plan:
        # O plano de edição define as pausas removidas (cada uma com justificativa no projeto).
        keep = direction.keep_intervals(plan, end - start)
        if keep:
            keep = media.snap_intervals(keep, media.output_fps(info) or info.get('fps') or 30)
    if prm['format'] != 'original':
        # Antes da legenda: sem as faixas pretas o vídeo muda de altura na tela, e a legenda se posiciona por ela.
        ctx.stage('enquadramento', 'Procurando faixas pretas no vídeo')
        box = media.detect_letterbox(folder / p['filename'], info, start, end, cancel_event=ctx.cancel_event,
                                     cache_dir=folder / 'cache')
        if box:
            info = {**info, 'letterbox': box}
            warnings.append(f"Faixas pretas do vídeo original removidas (área útil {box['w']}×{box['h']}).")
    if prm.get('subtitles') and prm.get('refine_subtitles', True):
        p = _refine_transcript(ctx, folder, p, info, start, end, warnings, title=prm.get('title'))
    width, height = media.target_size(prm['format'], info)
    segments = prm.get('framing_segments') if prm['framing'] == 'mix' else None
    kinds = {seg['framing'] for seg in segments or []}
    # Legenda: no "por trecho" com algum desfocado/quadro inteiro, fica abaixo da faixa do vídeo nesses
    # trechos; nos de seguir rosto (tela cheia) essa mesma altura continua sobre a imagem, sem borda.
    track = None
    used = kinds | {prm['framing']}
    # Área do streamer desenhada à mão: vira a "janela da webcam" (só o streamer e webcam + jogo), sem detecção.
    manual = reframe._visible_rect(prm['focus_rect'], info, info.get('letterbox')) if prm.get('focus_rect') else None
    if manual and 'track' not in used:
        track = {'ok': False, 'shots': [], 'facecam': manual, 'manual': True, 'message': ''}
        warnings.append('"Só o streamer" usou a área desenhada à mão neste corte.')
    elif prm['framing'] in ('track', 'track_blur', 'streamer', 'split') or kinds & {'track', 'streamer', 'split'}:
        # Antes da legenda: no "só o streamer" ela se posiciona pela altura da janela da webcam.
        ctx.stage('enquadramento', 'Analisando rostos para seguir quem fala')
        track = _cached_track(folder, p, info, start, end, prm['format'], ctx,
                              main_set_only=prm['framing'] == 'streamer')
        if manual:   # "por trecho" com seguir rosto e área à mão: rostos para uns, a área para os outros
            track = {**track, 'facecam': manual, 'manual': True, 'pip': None}
        if segments and 'streamer' in kinds and track['ok'] and not track.get('facecam'):
            # Por trecho com "só o streamer" sem janela de webcam: esses trechos seguem rostos só na cena
            # principal (bancada), como no modo "só o streamer"; os de "seguir rosto" continuam seguindo todos.
            main = _cached_track(folder, p, info, start, end, prm['format'], ctx, main_set_only=True)
            if main['ok'] and main.get('main_set_only'):
                track = {**track, 'main_set': main, 'main_set_only': True}
        if not track['ok']:
            warnings.append(track['message'])
        elif 'streamer' in kinds | {prm['framing']} and not track.get('facecam'):
            warnings.append('Nenhuma janela de webcam encontrada no corte; "só o streamer" seguiu os rostos só na '
                            'cena principal (bancada) e mostrou as outras cenas inteiras com fundo desfocado.'
                            if track.get('main_set_only') else
                            'Nenhuma janela de webcam encontrada no corte; "só o streamer" seguiu o rosto.')
        elif 'split' in kinds | {prm['framing']} and not track.get('facecam'):
            warnings.append('Nenhuma janela de webcam encontrada no corte; "webcam + jogo" seguiu o rosto.')
        elif 'split' in kinds | {prm['framing']} and prm['format'] != '9:16':
            warnings.append('"Webcam + jogo" é só para o formato vertical; neste formato o vídeo seguiu o rosto.')
    if segments:
        band_framing = ('blur' if kinds & {'blur', 'fit'} else 'split' if 'split' in kinds
                        else 'streamer' if 'streamer' in kinds else 'track')
    else:
        band_framing = prm['framing']
    band = media.content_band(prm['format'], band_framing, info, (width, height), (track or {}).get('facecam'))
    # Seguir rosto (o recorte ocupa a tela inteira): a legenda desvia da cabeça de quem é seguido.
    avoid = None
    if (band is None and not segments and prm['framing'] in ('track', 'streamer', 'split') and track
            and track.get('ok') and track.get('face_band') and not track.get('facecam')):
        avoid = tuple(round(v * height) for v in track['face_band'])   # cabeça, topo do rosto, queixo
    out_len = sum(b - a for a, b in keep) if keep else end - start
    hook_text = prm.get('hook_text') if prm.get('hook') else None
    sub_report = subtitles._new_report()
    rx = prm.get('reactions') or {}
    sfx = None
    if rx and not (prm.get('subtitles') and prm.get('subtitle_style') == 'dinamica'):
        warnings.append('Picos, som nos picos e destaques de reação só funcionam com a legenda animada.')
    if prm.get('subtitles'):
        style = subtitles.style_for(width, height)
        if not p.get('transcript'):
            cues = None
            warnings.append('Legendas não geradas: não há transcrição para este vídeo.')
        else:
            cues = subtitles.build_cues(p['transcript'], start, end, style['max_chars'])
            if not cues:
                warnings.append('Não há fala transcrita neste trecho; exportado sem legendas.')
        if cues and keep:
            for cue in cues:
                cue['start'] = round(media.remap_time(cue['start'], keep), 3)
                cue['end'] = round(media.remap_time(cue['end'], keep), 3)
            cues = [c for c in cues if c['end'] - c['start'] >= 0.15]
        if cues:
            srt_text = subtitles.to_srt(cues)
            if prm.get('subtitle_style') == 'dinamica':
                lines = subtitles.build_word_lines(p['transcript'], start, end)
                if plan:
                    marked = direction.emphasis_starts(plan)
                    for line in lines:
                        for w in line:
                            w['emph'] = round(w['start'], 2) in marked
                lines = _apply_reactions(ctx, folder, p, info, start, end, lines, rx, warnings)
                if keep:
                    lines = [[{**w, 'start': round(media.remap_time(w['start'], keep), 3),
                               'end': round(media.remap_time(w['end'], keep), 3)} for w in line] for line in lines]
                    lines = [[w for w in line if w['end'] - w['start'] >= 0.02] for line in lines]
                    lines = [line for line in lines if line]
                times = reactions.peak_times(lines)
                if times and rx.get('sound', 'none') in reactions.SOUNDS:
                    sfx = (times, rx['sound'])
                if lines:
                    ass_text = subtitles.to_ass_dynamic(lines, width, height, hook_text=hook_text, clip_len=out_len,
                                                        report=sub_report, video_band=band,
                                                        look=prm.get('subtitle_look'), avoid=avoid)
                else:
                    warnings.append('Legenda dinâmica indisponível (a transcrição não tem o tempo de cada palavra); '
                                    'usada a legenda clássica.')
            if ass_text is None:
                ass_text = subtitles.to_ass(cues, width, height, hook_text=hook_text, clip_len=out_len,
                                            report=sub_report, video_band=band, look=prm.get('subtitle_look'),
                                            avoid=avoid)
    if ass_text is None and hook_text:
        ass_text = subtitles.to_ass([], width, height, hook_text=hook_text, clip_len=out_len, report=sub_report,
                                    video_band=band, look=prm.get('subtitle_look'), avoid=avoid)
    post = audio_extra = ''
    look = filters.chain(prm.get('filter'))   # filtro de cor escolhido, antes da cor/zoom do plano
    if plan:
        remap = (lambda t: media.remap_time(t, keep)) if keep else (lambda t: t)
        post = direction.video_post_filters(plan, (width, height), media.output_fps(info) or info.get('fps') or 30,
                                            out_len, remap)
        audio_extra = direction.audio_filters(plan, out_len)
    post = look + post
    preview = bool(prm.get('preview'))
    ctx.stage('renderizando', 'Renderizando a prévia' if preview else 'Renderizando o corte')
    out_dir = folder / ('preview' if preview else 'clips')
    result = media.export_clip(folder / p['filename'], info, out_dir, f"clip_{int(prm['clip_id']):02d}",
                               start, end, fmt=prm['format'], framing=prm['framing'], ass_text=ass_text,
                               srt_text=None if preview else srt_text,
                               normalize_audio=bool(prm.get('normalize_audio')),
                               on_progress=lambda f, m='': ctx.progress(f),
                               cancel_event=ctx.cancel_event, keep=keep, track=track, post=post, audio_extra=audio_extra, preview=preview,
                               segments=segments, sfx=sfx)
    result['warnings'] = warnings + result['warnings']
    if sfx:
        result['peak_sound'] = {'sound': sfx[1], 'count': len(sfx[0])}
    # Verificação de qualidade visual registrada na exportação: a legenda cabe e o rosto não foi cortado.
    quality = {'subtitles': sub_report if sub_report['events'] else None,
               'reframe': (track or {}).get('metrics') if track and track.get('ok') else None}
    if quality['subtitles'] and quality['subtitles']['max_width_ratio'] > 1.0:
        result['warnings'].append('Uma legenda ficou mais larga que a área útil; confira o vídeo.')
    face_min = (quality['reframe'] or {}).get('face_visibility_min')
    if face_min is not None and face_min < 0.9:
        result['warnings'].append(f'O enquadramento deixa parte de um rosto fora do quadro em algum momento '
                                  f'(visibilidade mínima {face_min:.0%}).')
    result['quality'] = quality
    if plan:
        # Registro do plano usado: torna a exportação reproduzível.
        result['edit_plan'] = {'name': prm.get('edit_plan_name'), 'style': plan['style'],
                               'intensity': plan['intensity'], 'settings': plan['settings'],
                               'actions': [a for a in plan['actions'] if a.get('enabled', True)]}
    record = {**result, 'created_at': time.time(), 'job_id': ctx.id, 'title': prm.get('title'),
              'revision': prm.get('revision')}
    if preview:
        return _publish_preview(ctx, folder, prm, record)
    clips_dir = folder / 'clips'
    snapshot = storage.read_project(pid)
    clip_snapshot = next((c for c in snapshot.get('clips', []) if c['id'] == prm['clip_id']), {'id': prm['clip_id']})
    try:
        record['package_file'] = publish.write_package(clips_dir, snapshot, clip_snapshot, record)
    except OSError as exc:
        record['warnings'].append('Não foi possível gerar o pacote de publicação: ' + type(exc).__name__)
    if settings.load()['output']['enabled']:
        try:
            record['output'] = {'folder': publish.output_folder(snapshot).name,
                                'files': publish.copy_to_output(clips_dir, snapshot, clip_snapshot, record)}
        except OSError as exc:
            record['warnings'].append('Não foi possível copiar para a pasta de saída (' + type(exc).__name__ +
                                      '). O arquivo continua disponível no aplicativo.')
    stale: list[str] = []
    with storage.edit_project(pid) as p:
        clip = next((c for c in p.get('clips', []) if c['id'] == prm['clip_id']), None)
        if clip is None:
            stale.append(result['file'])
        else:
            previous = (clip.get('exports') or {}).get(prm['format'])
            if previous:
                stale += [previous.get('file'), previous.get('srt_file'), previous.get('package_file')]
            clip.setdefault('exports', {})[prm['format']] = record
            clip['export_file'] = result['file']
            clip['export_metadata'] = record
    for name in filter(None, stale):  # versões anteriores do mesmo formato (falha no Windows é tolerada)
        with contextlib.suppress(OSError):
            (folder / 'clips' / name).unlink()
    for message in record['warnings']:
        ctx.warn(message)
    return record


REFINE_VERSION = 2   # muda quando a retranscrição muda (v2: sem o contexto da frase anterior); refaz os cortes


def _refine_tag() -> str:
    return f'{config.WHISPER_REFINE_MODEL}:v{REFINE_VERSION}'


def _refined(p: dict, start: float, end: float) -> bool:
    model = _refine_tag()
    return any(a <= start + 0.05 and end - 0.05 <= b and m == model for a, b, m in p.get('transcript_refined') or [])


def _refine_prompt(project_name: str | None, title: str | None) -> str:
    """Dica de vocabulário para o modelo: nome do vídeo (sem data, resolução e extensão) e título do corte."""
    name = re.sub(r'\.[A-Za-z0-9]{2,4}$', '', project_name or '')
    words = [w for w in re.split(r'[\s_\-]+', name) if w and not any(ch.isdigit() for ch in w)]
    return '. '.join(part for part in (' '.join(words), (title or '').strip()) if part)


def _refine_transcript(ctx: JobContext, folder: Path, p: dict, info: dict, start: float, end: float,
                       warnings: list[str], *, title: str | None = None) -> dict:
    """Legenda precisa: retranscreve só o áudio do corte com o modelo maior e grava no projeto.

    As frases do projeto cujo meio cai dentro do corte são trocadas pelas novas (1 s de folga em cada ponta
    para não cortar palavras); o intervalo fica marcado e a próxima prévia/exportação não repete o trabalho.
    """
    if _refined(p, start, end) or info.get('audio_index') is None:
        return p
    # Mesmo quando a análise já usou o modelo grande vale retranscrever: na análise a live inteira vai em lotes,
    # sem o contexto da frase anterior e com busca menor (beam 3); só o corte, com contexto e beam 5, erra menos.
    if not transcription.refine_available():
        warnings.append('Legenda precisa indisponível (precisa de GPU NVIDIA com requirements-gpu.txt); '
                        'usada a transcrição da análise.')
        return p
    ctx.stage('legenda', f'Refinando a legenda ({config.WHISPER_REFINE_MODEL})')
    a, b = max(0.0, start - 1.0), min(info['duration'], end + 1.0)
    work = folder / 'work_legenda'
    try:
        work.mkdir(parents=True, exist_ok=True)
        wav = work / 'trecho.wav'
        media.run_ffmpeg(['-ss', f'{a:.3f}', '-t', f'{b - a:.3f}', '-i', str(folder / p['filename']),
                          '-map', f"0:{info['audio_index']}", '-vn', '-ac', '1', '-ar', '16000', str(wav)],
                         duration=b - a, cancel_event=ctx.cancel_event)
        segments, status = transcription.refine(wav, prompt=_refine_prompt(p.get('name'), title),
                                                cancel_event=ctx.cancel_event)
    except media.FFmpegError:
        segments, status = [], {'state': 'failed', 'message': 'Não foi possível extrair o áudio do corte.'}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if status['state'] != 'ok' or not segments:
        warnings.append(status.get('message') or 'Legenda precisa sem fala reconhecida; usada a da análise.')
        return p
    shift = lambda seg: {**seg, 'start': round(seg['start'] + a, 2), 'end': round(seg['end'] + a, 2),  # noqa: E731
                         'words': [{**w, 'start': round(w['start'] + a, 2), 'end': round(w['end'] + a, 2)}
                                   for w in seg.get('words') or []]}
    inside = lambda seg: start <= (seg['start'] + seg['end']) / 2 <= end  # noqa: E731
    fresh = [seg for seg in map(shift, segments) if inside(seg)]
    with storage.edit_project(ctx.project_id) as q:
        # Frases corrigidas à mão (``edited``) ficam: a retranscrição não as desfaz, e a nova não as cobre.
        edited = [seg for seg in q.get('transcript') or [] if inside(seg) and seg.get('edited')]
        fresh = [seg for seg in fresh if not any(seg['start'] < e['end'] and e['start'] < seg['end'] for e in edited)]
        kept = [seg for seg in q.get('transcript') or [] if not inside(seg) or seg.get('edited')]
        q['transcript'] = sorted(kept + fresh, key=lambda seg: seg['start'])
        q.setdefault('transcript_refined', []).append([round(start, 3), round(end, 3), _refine_tag()])
        updated = q
    return updated


def _apply_reactions(ctx: JobContext, folder: Path, p: dict, info: dict, start: float, end: float,
                     lines: list[list[dict]], rx: dict, warnings: list[str]) -> list[list[dict]]:
    """Vícios de fala, vitória/dinheiro e picos na legenda animada (``app/reactions.py``)."""
    if not rx:
        return lines
    if rx.get('fillers'):
        lines = reactions.clean_fillers(lines)
    wav = None
    if rx.get('peaks') and info.get('audio_index') is not None:
        ctx.stage('picos', 'Procurando os momentos de pico')
        wav = folder / 'cache' / f'.picos-{ctx.id}.wav'
        wav.parent.mkdir(parents=True, exist_ok=True)
        try:
            direction.extract_clip_audio(folder / p['filename'], info, start, end, wav, cancel_event=ctx.cancel_event)
        except media.FFmpegError:
            wav = None
    try:
        found = reactions.mark(lines, peaks=bool(rx.get('peaks')), wins=bool(rx.get('wins')), wav_path=wav,
                               duration=end - start)
    finally:
        if wav is not None:
            wav.unlink(missing_ok=True)
    if rx.get('peaks') and not found:
        warnings.append('Nenhum momento de pico claro neste corte (grito ou risada); a legenda saiu sem picos.')
    return reactions.isolate_peaks(lines)


def _cached_track(folder: Path, p: dict, info: dict, start: float, end: float, fmt: str, ctx: JobContext, *,
                  main_set_only: bool = False) -> dict:
    """Plano de enquadramento em cache por (trecho, formato, faixas pretas, versão do algoritmo, modo).

    ``main_set_only``: "só o streamer" numa transmissão sem janela de webcam segue rostos só na bancada.

    A análise de rostos é a etapa mais lenta: a prévia e a exportação do mesmo corte reaproveitam o resultado.
    """
    size = media.target_size(fmt, info)
    key = hashlib.sha256(json.dumps([reframe.PLAN_VERSION, p['filename'], round(start, 3), round(end, 3), size,
                                     info.get('letterbox'), len(p.get('scenes') or []),
                                     main_set_only]).encode()).hexdigest()[:24]
    cache = folder / 'direction' / f'track_{key}.json'
    with contextlib.suppress(OSError, ValueError):
        return json.loads(cache.read_text(encoding='utf-8'))
    track = reframe.plan(folder / p['filename'], info, start, end, size, scenes=p.get('scenes') or [],
                         cancel_event=ctx.cancel_event, main_set_only=main_set_only)
    with contextlib.suppress(OSError):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(track), encoding='utf-8')
    return track


def _publish_preview(ctx: JobContext, folder: Path, prm: dict, record: dict) -> dict:
    """Guarda só a prévia mais recente do corte: sem pacote, sem pasta de saída, sem histórico de exportações."""
    record = {**record, 'preview': True, 'start': prm['start'], 'end': prm['end'],
              'settings': {k: prm.get(k) for k in ('format', 'framing', 'subtitles', 'subtitle_style', 'hook',
                                                    'normalize_audio', 'shorten_pauses', 'edit_plan_name')}}
    stale = None
    with storage.edit_project(ctx.project_id) as p:
        clip = next((c for c in p.get('clips', []) if c['id'] == prm['clip_id']), None)
        if clip is None:
            stale = record['file']
        else:
            stale = (clip.get('preview') or {}).get('file')
            clip['preview'] = record
    if stale and stale != record['file']:
        with contextlib.suppress(OSError):
            (folder / 'preview' / stale).unlink()
    for message in record['warnings']:
        ctx.warn(message)
    return record


def end_export(ctx: JobContext, status: str, error: str | None) -> None:
    # Nada a desfazer: arquivos só são publicados após validação; temporários são removidos.
    return None
