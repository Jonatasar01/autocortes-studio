"""Orquestração do lote: uma pasta temporária por linha, limpa ao fim de cada iteração."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import config, media, reframe, transcription
from ..errors import AppError, Cancelled, sanitize
from ..audio import tts_generator
from . import captions, layouts
from .ingest import CutRow, format_timestamp, parse_csv
from .mastering import (AudioVisualMastering, MasteringError, MasteringSettings, audio_duration,
                        captions_from_narration, tpad)
from .slicer import slice_copy

# Vídeo narrado: se a narração passar do corte, o último quadro congela por no máximo isto.
MAX_FREEZE = 1.5

Log = Callable[[str], None]


@dataclass
class Options:
    output_dir: Path = Path('output')
    captions: bool = True
    max_words: int = 4
    font: str = 'Arial Black'
    uppercase: bool = True
    face_tracking: str = 'auto'   # auto | sim | nao (só para single_speaker)
    remove_bars: bool = True      # corta faixas pretas embutidas no original
    # Linhas com ``script`` (vídeo narrado):
    voice: str = tts_generator.DEFAULT_VOICE
    original_volume: float = 0.08  # áudio original ao fundo, bem abaixo da narração
    bgm: Path | None = None        # música de fundo opcional (repetida/cortada na duração da narração)
    bgm_volume: float = 0.05


@dataclass
class RowResult:
    row: CutRow
    ok: bool
    output: Path | None = None
    message: str = ''
    warnings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_').lower()[:60] or 'corte'


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f'{path.stem}_{n}{path.suffix}')
        if not candidate.exists():
            return candidate
    raise AppError('Não foi possível escolher um nome livre na pasta de saída.')


def _render_args(clip: Path, offset: float, duration: float, graph: str, has_audio: bool, out_name: str) -> list[str]:
    # -ss antes de -i num arquivo pequeno: busca exata (decodifica a partir do quadro-chave).
    args = ['-ss', f'{offset:.6f}', '-i', str(clip), '-t', f'{duration:.3f}',
            '-filter_complex', graph, '-map', '[vout]']
    if has_audio:
        args += ['-map', '0:a:0', '-af', 'aresample=async=1:first_pts=0',
                 '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2']
    else:
        args += ['-an']
    return args + ['-c:v', 'libx264', '-preset', config.FFMPEG_PRESET, '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-profile:v', 'high', '-movflags', '+faststart', '-avoid_negative_ts', 'make_zero',
                   '-map_metadata', '-1', '-sn', '-dn', out_name]


def _narrated_args(clip: Path, offset: float, video_duration: float, narration: Path, bgm: Path | None,
                   graph: str, total: float, out_name: str) -> list[str]:
    """Uma passagem: vídeo (0), narração (1) e música (2); a saída dura exatamente a narração."""
    args = ['-ss', f'{offset:.6f}', '-t', f'{video_duration:.3f}', '-i', str(clip), '-i', str(narration)]
    if bgm is not None:
        args += ['-stream_loop', '-1', '-i', str(bgm)]
    return args + ['-filter_complex', graph, '-map', '[vout]', '-map', '[aout]',
                   '-c:v', 'libx264', '-preset', config.FFMPEG_PRESET, '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-profile:v', 'high', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
                   '-t', f'{total:.3f}', '-movflags', '+faststart', '-map_metadata', '-1', '-sn', '-dn', out_name]


def _narration(row: CutRow, opts: Options, work: Path, cut: float, log: Log) -> tuple[Path, float, float]:
    """Gera a narração e devolve ``(arquivo, duração, segundos de vídeo usados)``."""
    voice = row.voice or opts.voice
    log(f'  gerando narração ({voice}, {len(row.script)} caracteres)')
    path = tts_generator.synthesize(row.script, work / 'narracao.mp3', voice=voice)
    total = audio_duration(path, 'A narração')
    if total - cut > MAX_FREEZE:
        raise AppError(f'a narração dura {total:.1f} s e o corte só {cut:.1f} s: aumente tempo_fim '
                       f'ou encurte o script.')
    return path, total, min(cut, total)


def process_row(row: CutRow, index: int, opts: Options, *, log: Log = print,
                cancel_event: threading.Event | None = None, source_info: dict | None = None) -> RowResult:
    result = RowResult(row=row, ok=False)
    if not row.video.is_file():
        result.message = f'vídeo não encontrado: {row.video}'
        return result
    info = source_info or media.probe_media(row.video)
    if row.start >= info['duration']:
        result.message = (f'tempo_inicio {format_timestamp(row.start)} está depois do fim do vídeo '
                          f'({format_timestamp(info["duration"])})')
        return result
    end = row.end
    if end > info['duration'] + 0.05:
        end = info['duration']
        result.warnings.append(f'tempo_fim ajustado ao fim do vídeo ({format_timestamp(end)}).')
    duration = end - row.start

    work = Path(tempfile.mkdtemp(prefix='autocortes-lote-'))
    try:
        narration = None
        total = video_duration = duration
        if row.narrated:
            # Antes do corte bruto: um roteiro longo demais para o trecho falha sem gastar tempo.
            narration, total, video_duration = _narration(row, opts, work, duration, log)
        log(f'  corte bruto {format_timestamp(row.start)} -> {format_timestamp(end)} (sem recodificar)')
        sliced = slice_copy(row.video, row.start, end, work / 'corte_temp.mp4', cancel_event=cancel_event)
        has_audio = sliced.info.get('audio_index') is not None

        subtitles_file = None
        if narration is not None and opts.captions and not transcription.available():
            result.warnings.append('faster-whisper não instalado (pip install -r requirements-ai.txt): '
                                   'vídeo narrado gerado sem legendas.')
        elif narration is not None and opts.captions:
            # Legenda SÓ da narração: as falas do vídeo original não entram.
            log('  transcrevendo a narração (faster-whisper)')
            ass, message = captions_from_narration(narration, work, max_words=opts.max_words, font=opts.font,
                                                   uppercase=opts.uppercase, cancel_event=cancel_event)
            if ass:
                (work / 'legenda.ass').write_text(ass, encoding='utf-8')
                subtitles_file = 'legenda.ass'
            else:
                result.warnings.append('sem legendas: ' + (message or 'narração não reconhecida.'))
        elif opts.captions and not has_audio:
            result.warnings.append('vídeo sem áudio: legendas não geradas.')
        elif opts.captions and not transcription.available():
            result.warnings.append('faster-whisper não instalado (pip install -r requirements-ai.txt): '
                                   'vídeo gerado sem legendas.')
        elif opts.captions:
            log('  transcrevendo o áudio do trecho (faster-whisper)')
            wav = captions.extract_audio(sliced.path, sliced.offset, duration, work / 'audio.wav',
                                         cancel_event=cancel_event)
            transcript, status = captions.transcribe(wav, duration, cancel_event=cancel_event)
            ass = captions.build_ass(transcript, duration, max_words=opts.max_words, font=opts.font,
                                     uppercase=opts.uppercase)
            if ass:
                (work / 'legenda.ass').write_text(ass, encoding='utf-8')
                subtitles_file = 'legenda.ass'
            else:
                result.warnings.append('sem legendas: ' + status.get('message', 'nenhuma fala reconhecida.'))

        letterbox = None
        if opts.remove_bars:
            letterbox = media.detect_letterbox(row.video, info, row.start, end, cancel_event=cancel_event)
            if letterbox:
                result.warnings.append(f"faixas pretas do original removidas (área útil "
                                       f"{letterbox['w']}x{letterbox['h']}).")
        clip_info = {**sliced.info, 'letterbox': letterbox} if letterbox else sliced.info

        track = None
        if row.layout == 'single_speaker' and opts.face_tracking != 'nao':
            if reframe.available():
                log('  analisando rostos para o enquadramento')
                track = reframe.plan(sliced.path, clip_info, sliced.offset, sliced.offset + video_duration,
                                     (layouts.OUT_W, layouts.OUT_H), cancel_event=cancel_event)
                if not track.get('ok'):
                    result.warnings.append('não foi possível seguir o rosto neste trecho; usado recorte central.')
                    track = None
            elif opts.face_tracking == 'sim':
                result.warnings.append('rastreamento de rosto requer OpenCV (requirements-ai.txt); '
                                       'usado recorte central.')

        log(f'  renderizando 1080x1920 ({row.layout}{", com legenda" if subtitles_file else ""}'
            f'{", narrado" if narration else ""})')
        master = AudioVisualMastering(MasteringSettings(original_volume=opts.original_volume,
                                                        bgm_volume=opts.bgm_volume))
        audio_graph = None
        if narration is not None:
            measured = master.measure(narration, cancel_event, total)
            audio_graph = master.narrated_audio_chain('[1:a:0]', '[0:a:0]' if has_audio else None,
                                                      '[2:a:0]' if opts.bgm else None, measured)

        def render(subs: str | None) -> None:
            if narration is None:
                graph = layouts.build(row.layout, subs, track, letterbox)
                args = _render_args(sliced.path, sliced.offset, duration, graph, has_audio, 'final.mp4')
            else:
                # Mesmo layout; o último quadro congela se a narração passar um pouco do corte, e a
                # legenda entra depois disso para continuar acompanhando a voz.
                video = layouts.build(row.layout, None, track, letterbox)[:-len('[vout]')]
                pad = tpad(total - video_duration) + (f',ass={subs}' if subs else '')
                args = _narrated_args(sliced.path, sliced.offset, video_duration, narration, opts.bgm,
                                      f'{video}{pad}[vout];{audio_graph}', total, 'final.mp4')
            media.run_ffmpeg(args, duration=total, cwd=work, cancel_event=cancel_event)

        try:
            render(subtitles_file)
        except media.FFmpegError:
            if not subtitles_file:
                raise
            # libass/fontes indisponíveis: entrega o vídeo sem legenda em vez de perder o corte.
            (work / 'final.mp4').unlink(missing_ok=True)
            result.warnings.append('a legenda não pôde ser embutida (FFmpeg/libass); vídeo gerado sem legenda.')
            render(None)

        report = media.validate_output(work / 'final.mp4', expected_duration=total,
                                       expected_size=(layouts.OUT_W, layouts.OUT_H),
                                       expect_audio=has_audio or narration is not None, cancel_event=cancel_event)
        if not report['valid']:
            failed = '; '.join(f"{c['check']}: {c['detail']}" for c in report['checks'] if not c['ok'])
            raise AppError('o vídeo gerado não passou na validação: ' + failed)
        result.warnings += report['warnings']

        opts.output_dir.mkdir(parents=True, exist_ok=True)
        base = f'{index:03d}_{_slug(row.name or row.video.stem)}_{row.layout}' + ('_narrado' if narration else '')
        final = _unique(opts.output_dir / f'{base}.mp4')
        shutil.move(str(work / 'final.mp4'), final)
        if subtitles_file:
            shutil.copyfile(work / subtitles_file, final.with_suffix('.ass'))
        if narration is not None:
            result.warnings.append(tts_generator.AI_VOICE_NOTICE)
        result.ok, result.output, result.message = True, final, 'ok'
    except Cancelled:
        raise
    except media.FFmpegError as exc:
        result.message = 'falha no FFmpeg: ' + sanitize(exc.stderr_tail[-400:], 400)
    except AppError as exc:
        result.message = exc.message
    except MasteringError as exc:
        result.message = str(exc).split('\n', 1)[0]
    finally:
        # Limpeza ao fim de cada iteração, com ou sem erro.
        media._remove_work_dir(work)
    return result


def run_batch(csv_path: Path, opts: Options, *, log: Log = print,
              cancel_event: threading.Event | None = None) -> tuple[list[RowResult], list[str]]:
    parsed = parse_csv(csv_path)
    for err in parsed.errors:
        log('IGNORADA ' + err)
    probes: dict[Path, dict | AppError] = {}
    results: list[RowResult] = []
    total = len(parsed.rows)
    for i, row in enumerate(parsed.rows, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        log(f'[{i}/{total}] linha {row.line}: {row.video.name} | {row.layout}')
        key = row.video.resolve()
        if key not in probes and row.video.is_file():
            try:
                probes[key] = media.probe_media(row.video)
            except AppError as exc:
                probes[key] = exc
        probe = probes.get(key)
        if isinstance(probe, AppError):
            res = RowResult(row=row, ok=False, message=probe.message)
        else:
            res = process_row(row, i, opts, log=log, cancel_event=cancel_event, source_info=probe)
        for w in res.warnings:
            log('  aviso: ' + w)
        log(f'  {"OK -> " + os.fspath(res.output) if res.ok else "ERRO: " + res.message}')
        results.append(res)
    return results, parsed.errors
