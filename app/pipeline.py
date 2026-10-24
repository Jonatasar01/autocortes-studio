"""Etapas de processamento executadas pela fila: análise do vídeo e exportação de cortes.

Nenhum projeto é marcado como ``ready`` e nenhuma exportação é registrada antes de os
resultados serem validados.
"""
from __future__ import annotations

import contextlib
import logging
import shutil
import time

from . import (curation, media, music, narration, publish, reframe, rights, selection, settings, storage,
               subtitles, transcription)
from .errors import AppError, Cancelled
from .jobs import JobContext

log = logging.getLogger('autocortes.pipeline')


# --------------------------------------------------------------------------- análise

def run_analysis(ctx: JobContext) -> dict:
    pid = ctx.project_id
    folder = storage.project_dir(pid)
    with storage.edit_project(pid) as p:
        p['status'] = 'analyzing'
        p['error'] = None
        p['analysis_job'] = ctx.id
        source = folder / p['filename']
        opts = {k: p.get(k) for k in ('source_rights', 'external_audio', 'license_notes', 'graphics_rights',
                                      'third_party_footage', 'monetization', 'recognize_music',
                                      'target_seconds', 'desired_count')}

    ctx.stage('validando', 'Validando o arquivo de vídeo')
    info = media.probe_media(source)
    warnings = media.integrity_warnings(source, info)
    ctx.progress(0.03)

    ctx.stage('musica', 'Verificando música (somente se autorizado)')
    music_review = music.recognize_music(source, info['duration'], bool(opts['recognize_music']), info['audio'],
                                         cancel_event=ctx.cancel_event, audio_index=info.get('audio_index'))
    review = rights.rights_review(opts['source_rights'], opts['external_audio'], opts['license_notes'] or '',
                                  info['audio'], opts.get('graphics_rights') or 'unknown',
                                  opts.get('third_party_footage') or 'unknown', opts.get('monetization') or 'unknown',
                                  music_review)
    with storage.edit_project(pid) as p:
        p.update({'media': info, 'rights': review, 'music_review': music_review, 'warnings': warnings})
    ctx.progress(0.08)

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
        except media.FFmpegError as exc:
            t_status = {'state': 'failed', 'message': 'Não foi possível extrair o áudio; cortes temporais. '
                        + exc.args[0]}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    ctx.progress(0.75)

    ctx.stage('cenas', 'Detectando mudanças de cena')
    scenes = media.detect_scenes(source, info, folder / 'work', on_progress=ctx.span(0.75, 0.88),
                                 cancel_event=ctx.cancel_event)
    shutil.rmtree(folder / 'work', ignore_errors=True)

    ctx.stage('selecionando', 'Selecionando os melhores trechos')
    fallback = None if t_status['state'] == 'ok' else t_status['message']
    clips, sel = selection.select_clips(transcript, info['duration'], opts['target_seconds'], opts['desired_count'],
                                        scenes, fallback_reason=fallback)
    ctx.progress(0.9)

    live_cfg = settings.load()['curation']
    cur_cfg = dict(storage.read_project(pid).get('curation_profile') or live_cfg)
    # Um perfil congelado não pode restaurar consentimento posteriormente revogado.
    if not live_cfg['consent'] or live_cfg['provider'] != cur_cfg['provider']:
        cur_cfg['consent'] = False
    provider = cur_cfg['provider']
    curation_status = {'state': 'disabled', 'provider': 'heuristica',
                       'message': 'Curadoria com IA na nuvem desativada (Configurações).'}
    if provider != 'heuristica' and sel['mode'] != 'transcricao':
        curation_status = {'state': 'skipped', 'provider': provider,
                           'message': 'Curadoria com IA não usada: não há transcrição para avaliar.'}
    elif provider != 'heuristica' and not cur_cfg['consent']:
        curation_status = {'state': 'skipped', 'provider': provider,
                           'message': 'Curadoria com IA não usada: falta o consentimento nas Configurações.'}
    elif provider != 'heuristica':
        ctx.stage('curadoria_ia', f'Escolhendo e avaliando trechos com IA ({curation.PROVIDER_LABELS[provider]})')
        with storage.edit_project(pid) as p:
            p.setdefault('cloud_consents', []).append({
                'granted_at': cur_cfg['consent_at'], 'used_at': time.time(), 'provider': curation.PROVIDER_LABELS[provider],
                'model': curation.model_for(provider), 'scope': 'curadoria de cortes (somente texto da transcrição)',
                'source': 'Configurações'})
        try:
            ai_clips, curation_status = curation.curate(
                provider, transcript, info['duration'], opts['target_seconds'], opts['desired_count'], scenes,
                prompt=cur_cfg.get('prompt') or '', cancel_event=ctx.cancel_event, on_progress=ctx.span(0.9, 0.99))
            clips = ai_clips
        except Cancelled:
            raise
        except AppError as exc:
            curation_status = {'state': 'failed', 'provider': provider,
                               'message': f'Curadoria com IA falhou ({exc.message}); mantida a seleção heurística.'}
            ctx.warn(curation_status['message'])
    if curation_status['state'] == 'ok':
        ranker = {'state': 'skipped', 'message': 'Ranking feito pela curadoria com IA.'}
    else:
        ctx.stage('ia_local', 'Refinando com IA local (se disponível)')
        ranker = selection.refine_with_ollama(clips, cancel_event=ctx.cancel_event, on_progress=ctx.span(0.9, 0.99))

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
    start, end = float(prm['start']), min(float(prm['end']), info['duration'])
    ctx.stage('preparando', 'Preparando exportação')
    # Estimativa conservadora: ~12 Mbit/s + arquivo temporário
    storage.ensure_free_space(int((end - start) * 1.5 * 1024 ** 2 * 2) + 100 * 1024 ** 2, folder)
    ass_text = srt_text = None
    warnings: list[str] = []
    placed: list[dict] | None = None
    if prm.get('narration'):
        clip_now = next((c for c in p.get('clips', []) if c['id'] == prm['clip_id']), {})
        script = prm.get('narration_script') or clip_now.get('narration') or {}
        if not script.get('segments'):
            raise AppError('Gere o roteiro de narração deste corte antes de exportar com narração.', 409, 'no_script')
        if abs(script.get('clip_start', start) - start) > 0.05 or abs(script.get('clip_end', end) - end) > 0.05:
            warnings.append('Os tempos do corte mudaram depois que o roteiro foi gerado; confira se a narração '
                            'continua alinhada às cenas.')
        ctx.stage('narracao', 'Gerando a voz da narração (OpenAI)')
        voiced = narration.synthesize(script['segments'], style=script.get('style', 'recap'),
                                      voice=prm.get('voice') or 'cedar', cache_dir=folder / 'narration',
                                      cancel_event=ctx.cancel_event, on_progress=ctx.span(0.0, 0.25))
        placed, place_warnings = narration.place(voiced, end - start)
        warnings += place_warnings
        if not placed:
            raise AppError('Nenhum bloco de narração cabe no trecho.', 422, 'no_script')
        warnings.append(narration.AI_VOICE_NOTICE)
        if (p.get('rights') or {}).get('level') == 'alto':
            warnings.append(narration.THIRD_PARTY_NOTICE)
    keep = None
    if prm.get('shorten_pauses') and not placed:
        ctx.stage('pausas', 'Encurtando pausas longas')
        keep = (media.pause_intervals(p.get('transcript') or [], start, end) or
                media.silence_intervals(folder / p['filename'], info, start, end - start, cancel_event=ctx.cancel_event))
        if keep:
            keep = media.snap_intervals(keep, media.output_fps(info) or info.get('fps') or 30)
        else:
            warnings.append('Nenhuma pausa longa encontrada neste trecho; exportado sem encurtar.')
    width, height = media.target_size(prm['format'], info)
    out_len = sum(b - a for a, b in keep) if keep else end - start
    hook_text = prm.get('hook_text') if prm.get('hook') else None
    if prm.get('subtitles'):
        style = subtitles.style_for(width, height)
        narration_source = bool(placed) and prm.get('subtitle_source', 'narracao') == 'narracao'
        if narration_source:
            cues = subtitles.build_cues([{'start': x['start'], 'end': x['end'], 'text': x['text']} for x in placed],
                                        0, end - start, style['max_chars'])
        elif not p.get('transcript'):
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
            if prm.get('subtitle_style') == 'dinamica' and not narration_source:
                lines = subtitles.build_word_lines(p['transcript'], start, end)
                if keep:
                    lines = [[{**w, 'start': round(media.remap_time(w['start'], keep), 3),
                               'end': round(media.remap_time(w['end'], keep), 3)} for w in line] for line in lines]
                    lines = [[w for w in line if w['end'] - w['start'] >= 0.02] for line in lines]
                    lines = [line for line in lines if line]
                if lines:
                    ass_text = subtitles.to_ass_dynamic(lines, width, height, hook_text=hook_text, clip_len=out_len)
                else:
                    warnings.append('Legenda dinâmica indisponível (a transcrição não tem o tempo de cada palavra); '
                                    'usada a legenda clássica.')
            elif prm.get('subtitle_style') == 'dinamica':
                warnings.append('Legenda dinâmica usa a fala transcrita; com legenda da narração foi usada a clássica.')
            if ass_text is None:
                ass_text = subtitles.to_ass(cues, width, height, hook_text=hook_text, clip_len=out_len)
    if ass_text is None and hook_text:
        ass_text = subtitles.hook_only_ass(width, height, hook_text, clip_len=out_len)
    if prm['format'] != 'original':
        ctx.stage('enquadramento', 'Procurando faixas pretas no vídeo')
        box = media.detect_letterbox(folder / p['filename'], info, start, end, cancel_event=ctx.cancel_event)
        if box:
            info = {**info, 'letterbox': box}
            warnings.append(f"Faixas pretas do vídeo original removidas (área útil {box['w']}×{box['h']}).")
    track = None
    if prm['framing'] == 'track':
        ctx.stage('enquadramento', 'Analisando rostos para seguir quem fala')
        track = reframe.plan(folder / p['filename'], info, start, end, media.target_size(prm['format'], info),
                             scenes=p.get('scenes') or [], cancel_event=ctx.cancel_event)
        if not track['ok']:
            warnings.append(track['message'])
    ctx.stage('renderizando', 'Renderizando o corte')
    result = media.export_clip(folder / p['filename'], info, folder / 'clips', f"clip_{int(prm['clip_id']):02d}",
                               start, end, fmt=prm['format'], framing=prm['framing'], ass_text=ass_text,
                               srt_text=srt_text, normalize_audio=bool(prm.get('normalize_audio')),
                               on_progress=(ctx.span(0.25, 1.0) if placed else (lambda f, m='': ctx.progress(f))),
                               cancel_event=ctx.cancel_event, narration=placed, keep=keep,
                               original_volume=max(0, min(100, int(prm.get('original_volume', 100)))) / 100,
                               track=track)
    result['warnings'] = warnings + result['warnings']
    record = {**result, 'created_at': time.time(), 'job_id': ctx.id, 'title': prm.get('title'),
              'revision': prm.get('revision')}
    if placed:
        record['narration'] = {'voice': prm.get('voice'), 'tts_model': narration.config.OPENAI_TTS_MODEL,
                               'blocks': len(placed), 'ai_voice': True}
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


def end_export(ctx: JobContext, status: str, error: str | None) -> None:
    # Nada a desfazer: arquivos só são publicados após validação; temporários são removidos.
    return None


# --------------------------------------------------------------------------- roteiro de narração (OpenAI)

def run_narration(ctx: JobContext) -> dict:
    pid, prm = ctx.project_id, ctx.params
    folder = storage.project_dir(pid)
    p = storage.read_project(pid)
    if p.get('status') != 'ready':
        raise AppError('O projeto não está pronto.', 409, 'not_ready')
    clip = next((c for c in p.get('clips', []) if c['id'] == prm['clip_id']), None)
    if clip is None:
        raise AppError('Trecho não encontrado.', 404, 'not_found')
    ctx.stage('roteiro', 'Escrevendo o roteiro com ChatGPT')
    ctx.progress(0.1)
    script = narration.generate_script(folder / p['filename'], p['media'], prm['start'], prm['end'],
                                       p.get('transcript') or [], style=prm['style'], context=prm.get('context', ''),
                                       use_frames=bool(prm.get('use_frames')), cancel_event=ctx.cancel_event)
    with storage.edit_project(pid) as p:
        target = next((c for c in p.get('clips', []) if c['id'] == prm['clip_id']), None)
        if target is not None:
            if (target['start'], target['end']) != (prm['start'], prm['end']):
                raise AppError('O corte foi alterado durante a geração. Gere o roteiro novamente para os novos tempos.',
                               409, 'stale_script')
            target['narration'] = script
    for message in script['warnings']:
        ctx.warn(message)
    return {'segments': len(script['segments']), 'title': script['title']}
