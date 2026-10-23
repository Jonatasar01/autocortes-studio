"""Camada de mídia: inspeção (ffprobe), execução do FFmpeg com progresso/cancelamento,
exportação de cortes e validação real do arquivo gerado.

Regras:
- Subprocessos sempre com lista de argumentos (sem shell) — sem command injection.
- O resultado só é publicado (renomeação atômica) depois de validado.
- Nenhum caminho do usuário é interpolado dentro de filtros do FFmpeg: legendas e
  arquivos auxiliares ficam em uma pasta temporária usada como diretório de trabalho.
"""
from __future__ import annotations

import collections
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Callable

from . import config, storage
from .errors import AppError, Cancelled, sanitize

ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}
FORMATS = ('9:16', '16:9', '1:1', 'original')
FRAMINGS = ('fit', 'blur', 'track')  # track = seguir rosto (recorta as laterais)
FORMAT_SLUG = {'9:16': '9x16', '16:9': '16x9', '1:1': '1x1', 'original': 'orig'}

ProgressFn = Callable[[float, str], None]


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr_tail: str = ''):
        super().__init__(message)
        self.stderr_tail = stderr_tail


def require_binary(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise AppError(f'{name} não encontrado. Instale o FFmpeg e adicione-o à variável PATH.',
                       500, 'ffmpeg_missing')
    return executable


def _priority_prefix() -> list[str]:
    if config.LOW_PRIORITY and os.name == 'posix' and shutil.which('nice'):
        return ['nice', '-n', '10']
    return []


def _popen_kwargs() -> dict:
    if os.name == 'nt':
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if config.LOW_PRIORITY:
            flags |= getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        return {'creationflags': flags}
    return {}


def run(args: list[str], timeout: float = 120, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Execução curta e síncrona (ffprobe, verificações)."""
    return subprocess.run(args, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, check=True, **_popen_kwargs())


def _parse_rate(value: str | None) -> float:
    try:
        rate = float(Fraction(str(value)))
        return rate if math.isfinite(rate) and rate > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _parse_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _rotation(stream: dict) -> int:
    for item in stream.get('side_data_list', []) or []:
        if 'rotation' in item:
            try:
                return int(round(float(item['rotation']))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int(stream.get('tags', {}).get('rotate', 0)) % 360
    except (TypeError, ValueError):
        return 0


def _ffprobe_json(path: Path, timeout: float = 60) -> dict:
    try:
        out = run([require_binary('ffprobe'), '-v', 'error', '-print_format', 'json',
                   '-show_format', '-show_streams', str(path)], timeout=timeout).stdout
        return json.loads(out or '{}')
    except subprocess.TimeoutExpired as exc:
        raise AppError('A leitura do arquivo demorou demais. Ele pode estar danificado ou em um disco lento.',
                       422, 'probe_timeout') from exc
    except subprocess.CalledProcessError as exc:
        raise AppError('O arquivo não pôde ser lido como vídeo. Ele pode estar corrompido, incompleto '
                       'ou em um formato não suportado.', 422, 'invalid_media',
                       {'technical': sanitize(exc.stderr, 300)}) from exc
    except json.JSONDecodeError as exc:
        raise AppError('Resposta inválida do ffprobe ao ler o vídeo.', 422, 'invalid_media') from exc


def probe_media(path: Path) -> dict:
    """Valida o conteúdo real do arquivo (não apenas a extensão) e retorna metadados."""
    data = _ffprobe_json(path)
    streams = data.get('streams', []) or []
    fmt = data.get('format', {}) or {}
    videos = [s for s in streams if s.get('codec_type') == 'video'
              and not (s.get('disposition') or {}).get('attached_pic')]
    if not videos:
        raise AppError('O arquivo não contém uma trilha de vídeo (pode ser apenas áudio ou imagem de capa).',
                       422, 'no_video')
    video = videos[0]
    width, height = int(video.get('width') or 0), int(video.get('height') or 0)
    if width <= 0 or height <= 0:
        raise AppError('Não foi possível identificar a resolução do vídeo.', 422, 'invalid_media')
    if max(width, height) > config.MAX_DIMENSION:
        raise AppError(f'Resolução {width}×{height} acima do limite suportado ({config.MAX_DIMENSION} px).',
                       422, 'too_large')
    rotation = _rotation(video)
    display_w, display_h = (height, width) if rotation in (90, 270) else (width, height)
    duration = (_parse_float(fmt.get('duration')) or _parse_float(video.get('duration')))
    if not duration:
        raise AppError('Não foi possível identificar a duração do vídeo. O arquivo pode estar incompleto.',
                       422, 'no_duration')
    if duration > config.MAX_SOURCE_SECONDS:
        raise AppError(f'Vídeo com {duration / 3600:.1f} h excede o limite de '
                       f'{config.MAX_SOURCE_SECONDS // 3600} h.', 422, 'too_long')
    if duration < 1:
        raise AppError('O vídeo tem menos de 1 segundo e não pode gerar cortes.', 422, 'too_short')
    avg = _parse_rate(video.get('avg_frame_rate'))
    real = _parse_rate(video.get('r_frame_rate'))
    fps = avg or real
    vfr = bool(avg and real and abs(avg - real) / real > 0.02)
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    default_audio = next((s for s in audios if (s.get('disposition') or {}).get('default')),
                         audios[0] if audios else None)
    if display_h > display_w * 1.05:
        orientation = 'vertical'
    elif display_w > display_h * 1.05:
        orientation = 'horizontal'
    else:
        orientation = 'quadrado'
    return {
        'duration': round(duration, 3),
        'width': display_w, 'height': display_h,
        'coded_width': width, 'coded_height': height, 'rotation': rotation,
        'orientation': orientation,
        'fps': round(fps, 3) if fps else None, 'vfr': vfr,
        'video_codec': video.get('codec_name'), 'pix_fmt': video.get('pix_fmt'),
        'video_index': int(video.get('index', 0)),
        'audio': bool(audios), 'audio_streams': len(audios),
        'audio_index': int(default_audio['index']) if default_audio else None,
        'audio_codec': default_audio.get('codec_name') if default_audio else None,
        'container': fmt.get('format_name'),
        'size_bytes': int(fmt.get('size') or 0) or (path.stat().st_size if path.exists() else 0),
        'bit_rate': int(fmt.get('bit_rate') or 0) or None,
    }


def integrity_warnings(path: Path, media: dict) -> list[str]:
    """Decodifica rapidamente o início e o fim. Falha no início é erro; no fim, aviso."""
    ffmpeg = require_binary('ffmpeg')

    def frames(seek: float) -> tuple[int, str]:
        cmd = [ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-ss', f'{seek:.3f}', '-i', str(path),
               '-t', '2', '-map', f"0:{media['video_index']}", '-progress', 'pipe:1', '-f', 'null', '-']
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    errors='replace', timeout=120, **_popen_kwargs())
        except subprocess.TimeoutExpired:
            return 0, 'timeout'
        found = re.findall(r'^frame=(\d+)', result.stdout, re.M)
        return (int(found[-1]) if found else 0), result.stderr

    first, err = frames(0)
    if first == 0:
        raise AppError('Não foi possível decodificar o início do vídeo. O arquivo parece corrompido.',
                       422, 'decode_failed', {'technical': sanitize(err, 300)})
    warnings = []
    if media['duration'] > 6:
        last, _ = frames(max(0.0, media['duration'] - 3))
        if last == 0:
            warnings.append('O final do arquivo não pôde ser decodificado: ele pode estar incompleto. '
                            'Cortes próximos ao fim podem falhar.')
    if media.get('vfr'):
        warnings.append('Vídeo com taxa de quadros variável: a exportação converte para taxa constante '
                        'para manter áudio e vídeo sincronizados.')
    if media.get('audio_streams', 0) > 1:
        warnings.append(f"O arquivo tem {media['audio_streams']} trilhas de áudio; será usada a trilha padrão.")
    return warnings


def run_ffmpeg(args: list[str], *, duration: float, cwd: Path | None = None,
               on_progress: ProgressFn | None = None, cancel_event: threading.Event | None = None,
               timeout: float | None = None) -> str:
    """Executa o FFmpeg com progresso real (-progress) e cancelamento. Retorna o stderr final."""
    cmd = _priority_prefix() + [require_binary('ffmpeg'), '-hide_banner', '-nostdin', '-y',
                                '-progress', 'pipe:1', '-nostats']
    if config.FFMPEG_THREADS:
        cmd += ['-threads', str(config.FFMPEG_THREADS)]
    cmd += args
    deadline = time.monotonic() + (timeout or max(600.0, duration * 40))
    proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                            **_popen_kwargs())
    tail: collections.deque[str] = collections.deque(maxlen=60)
    def read_stderr():
        for line in proc.stderr:
            tail.append(line.rstrip())

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()

    def stop():
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # Um vigia garante cancelamento/timeout mesmo se o FFmpeg parar de emitir progresso.
    stopped = {'reason': None}

    def watchdog():
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                stopped['reason'] = 'cancel'
                stop()
                return
            if time.monotonic() > deadline:
                stopped['reason'] = 'timeout'
                stop()
                return
            time.sleep(0.25)

    guard = threading.Thread(target=watchdog, daemon=True)
    guard.start()
    try:
        for line in proc.stdout:
            key, _, value = line.strip().partition('=')
            if key in ('out_time_us', 'out_time_ms') and on_progress and duration > 0:
                try:
                    seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, seconds / duration)), '')
        proc.wait()
    finally:
        if proc.poll() is None:
            stop()
        guard.join(timeout=6)
        reader.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):  # evita vazamento de descritores em uso contínuo
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    stderr = '\n'.join(tail)
    if stopped['reason'] == 'cancel':
        raise Cancelled()
    if stopped['reason'] == 'timeout':
        raise FFmpegError('O FFmpeg excedeu o tempo limite.', stderr)
    if proc.returncode != 0:
        raise FFmpegError(f'O FFmpeg terminou com erro (código {proc.returncode}).', stderr)
    if on_progress:
        on_progress(1.0, '')
    return stderr


# --------------------------------------------------------------------------- áudio auxiliar

def extract_audio_wav(source: Path, media: dict, dest: Path, *, on_progress: ProgressFn | None = None,
                      cancel_event: threading.Event | None = None) -> Path:
    """Extrai a trilha padrão em WAV 16 kHz mono para a transcrição local."""
    if media.get('audio_index') is None:
        raise AppError('Vídeo sem áudio.', 422, 'no_audio')
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(['-i', str(source), '-map', f"0:{media['audio_index']}", '-vn', '-ac', '1', '-ar', '16000',
                '-c:a', 'pcm_s16le', str(dest)], duration=media['duration'], on_progress=on_progress,
               cancel_event=cancel_event)
    return dest


# --------------------------------------------------------------------------- cenas

def detect_scenes(source: Path, media: dict, work_dir: Path, *, on_progress: ProgressFn | None = None,
                  cancel_event: threading.Event | None = None, threshold: float = 0.32) -> list[float]:
    """Detecção de mudança de cena via FFmpeg (sem dependências extras)."""
    if os.getenv('AUTOCORTES_SCENES', '1') == '0':
        return []
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / 'scenes.txt'
    vf = f"fps=4,scale=192:-2,select='gt(scene,{threshold})',metadata=print:file=scenes.txt"
    try:
        run_ffmpeg(['-i', str(source), '-map', f"0:{media['video_index']}", '-an', '-vf', vf, '-f', 'null', '-'],
                   duration=media['duration'], cwd=work_dir, on_progress=on_progress, cancel_event=cancel_event)
    except FFmpegError:
        return []
    if not out.exists():
        return []
    times = [float(m) for m in re.findall(r'pts_time:([0-9.]+)', out.read_text(errors='replace'))]
    out.unlink(missing_ok=True)
    result: list[float] = []
    for value in sorted(times):
        if 0 < value < media['duration'] and (not result or value - result[-1] >= 1.0):
            result.append(round(value, 2))
    return result


# --------------------------------------------------------------------------- exportação

def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def target_size(fmt: str, media: dict) -> tuple[int, int]:
    w, h = media['width'], media['height']
    if fmt == '9:16':
        return (1080, 1920) if max(w, h) >= 1280 else (720, 1280)
    if fmt == '16:9':
        return (1920, 1080) if max(w, h) >= 1600 or min(w, h) >= 1000 else (1280, 720)
    if fmt == '1:1':
        return (1080, 1080) if min(w, h) >= 720 else (720, 720)
    if fmt == 'original':
        scale = min(1.0, 1920 / max(w, h))
        return _even(w * scale), _even(h * scale)
    raise AppError('Formato de exportação inválido.', 400, 'bad_format')


def output_fps(media: dict) -> float | None:
    """Força taxa constante quando a origem é VFR ou acima de 60 fps."""
    fps = media.get('fps') or 0
    if media.get('vfr') or fps > 60 or fps <= 0:
        return float(min(60, max(1, round(fps)))) if fps > 0 else 30.0
    return None


# --------------------------------------------------------------------------- encurtar pausas

def snap_intervals(keep: list[tuple[float, float]], fps: float) -> list[tuple[float, float]]:
    """Alinha os intervalos à grade de quadros (evita deriva entre áudio e vídeo a cada corte)."""
    out = []
    for a, b in keep:
        a2, b2 = round(a * fps) / fps, round(b * fps) / fps
        if b2 - a2 >= 2 / fps:
            out.append((round(a2, 6), round(b2, 6)))
    return out


def pause_intervals(transcript: list[dict], start: float, end: float, *, max_gap: float = 0.7,
                    pad: float = 0.15) -> list[tuple[float, float]] | None:
    """Trechos a manter (tempos relativos ao corte), encurtando pausas maiores que ``max_gap``."""
    clip_len = end - start
    spans = []
    for seg in transcript:
        if seg['end'] <= start or seg['start'] >= end:
            continue
        words = [w for w in seg.get('words') or [] if 'start' in w and 'end' in w]
        for item in (words or [seg]):
            a, b = max(0.0, item['start'] - start), min(clip_len, item['end'] - start)
            if b > a:
                spans.append((a, b))
    if not spans:
        return None
    spans.sort()
    keep: list[list[float]] = []
    first_a = spans[0][0]
    cur_a = max(0.0, first_a - pad) if first_a > max_gap else 0.0
    prev_end = spans[0][1]
    for a, b in spans[1:]:
        if a - prev_end > max_gap:
            keep.append([cur_a, prev_end + pad])
            cur_a = a - pad
        prev_end = max(prev_end, b)
    tail = clip_len if clip_len - prev_end <= max_gap else min(clip_len, prev_end + pad * 2)
    keep.append([cur_a, tail])
    result = [(round(a, 3), round(min(b, clip_len), 3)) for a, b in keep if b - a > 0.2]
    removed = clip_len - sum(b - a for a, b in result)
    return result if removed >= 0.5 else None


def silence_intervals(source: Path, media: dict, start: float, duration: float, *, max_gap: float = 0.7,
                      pad: float = 0.15, cancel_event: threading.Event | None = None) -> list[tuple[float, float]] | None:
    """Sem transcrição: usa o silencedetect do FFmpeg no áudio do corte."""
    if media.get('audio_index') is None:
        return None
    try:
        stderr = run_ffmpeg(['-loglevel', 'info', '-ss', f'{start:.3f}', '-i', str(source), '-t', f'{duration:.3f}',
                             '-map', f"0:{media['audio_index']}", '-af', f'silencedetect=n=-35dB:d={max_gap}',
                             '-f', 'null', '-'], duration=duration, cancel_event=cancel_event)
    except FFmpegError:
        return None
    starts = [float(x) for x in re.findall(r'silence_start:\s*(-?[0-9.]+)', stderr)]
    ends = [float(x) for x in re.findall(r'silence_end:\s*([0-9.]+)', stderr)]
    keep, cursor = [], 0.0
    for i, s_start in enumerate(starts):
        s_end = ends[i] if i < len(ends) else duration
        a, b = max(0.0, s_start + pad), min(duration, s_end - pad)
        if b - a <= 0.2:
            continue
        if a > cursor:
            keep.append((round(cursor, 3), round(a, 3)))
        cursor = b
    if cursor < duration:
        keep.append((round(cursor, 3), round(duration, 3)))
    keep = [(a, b) for a, b in keep if b - a > 0.2]
    removed = duration - sum(b - a for a, b in keep)
    return keep if keep and removed >= 0.5 else None


def remap_time(t: float, keep: list[tuple[float, float]]) -> float:
    """Converte um instante da linha do tempo original do corte para a linha do tempo encurtada."""
    offset = 0.0
    for a, b in keep:
        if t < a:
            return offset
        if t <= b:
            return offset + (t - a)
        offset += b - a
    return offset


def _select_expr(keep: list[tuple[float, float]]) -> str:
    return '+'.join(f'gte(t,{a:.6f})*lt(t,{b:.6f})' for a, b in keep)


def _blur_branch(inp: str, W: int, H: int, out: str) -> str:
    bw, bh = _even(W / 4), _even(H / 4)
    return (f'{inp}split=2[bgsrc][fgsrc];'
            f'[bgsrc]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},'
            f'boxblur=8:2,scale={W}:{H},eq=brightness=-0.06[bg];'
            f'[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bicubic[fg];'
            f'[bg][fg]overlay=(W-w)/2:(H-h)/2{out}')


def _track_graph(src: str, pre: str, subs: str, W: int, H: int, track: dict) -> str:
    """Recorte que segue o rosto (expressão de tempo no crop) e fundo desfocado nas cenas sem rosto."""
    from . import reframe
    x = f"clip(({reframe.crop_x_expression(track['shots'])})*iw-ow/2,0,iw-ow)"
    crop = f"crop=w='2*trunc(ih*{W}/{H}/2)':h=ih:x='{x}':y=0,scale={W}:{H}:flags=bicubic,setsar=1"
    enable = reframe.fit_enable_expression(track['shots'])
    if not enable:
        return f'{src}{crop},{pre}setsar=1{subs}[vout]'
    return (f'{src}split=2[tsrc][fsrc];[tsrc]{crop}[trk];'
            + _blur_branch('[fsrc]', W, H, ',setsar=1[fitv]') +
            f";[trk][fitv]overlay=0:0:enable='{enable}'[mix];[mix]{pre}setsar=1{subs}[vout]")


def build_filtergraph(media: dict, fmt: str, framing: str, subtitles_file: str | None,
                      keep: list[tuple[float, float]] | None = None,
                      track: dict | None = None) -> tuple[str, tuple[int, int]]:
    W, H = target_size(fmt, media)
    src = f"[0:{media['video_index']}]"
    pre = f'fps={output_fps(media):g},' if output_fps(media) else ''
    if keep:
        fps = output_fps(media) or media.get('fps') or 30
        pre = f"fps={fps:g},select='{_select_expr(keep)}',setpts=N/FRAME_RATE/TB,"
    subs = f',ass={subtitles_file}' if subtitles_file else ''
    if fmt == 'original':
        graph = f'{src}{pre}scale={W}:{H}:flags=bicubic,setsar=1{subs}[vout]'
    elif framing == 'track' and track and track.get('ok'):
        graph = _track_graph(src, pre, subs, W, H, track)
    elif framing in ('blur', 'track'):  # track sem plano válido: fundo desfocado
        graph = _blur_branch(f'{src}{pre}', W, H, f',setsar=1{subs}[vout]')
    else:  # fit: enquadramento original preservado com barras
        graph = (f'{src}{pre}scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bicubic,'
                 f'pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1{subs}[vout]')
    return graph, (W, H)


def _narration_audio_graph(media: dict, narration: list[dict], original_volume: float, normalize_audio: bool) -> str:
    """Mixa os blocos de narração (entradas 1..n) com o áudio original abaixado durante a fala."""
    parts = []
    labels = ''
    for i, item in enumerate(narration):
        delay = int(round(item['start'] * 1000))
        tempo = f",atempo={item['tempo']:.3f}" if item.get('tempo', 1) > 1.001 else ''
        parts.append(f'[{i + 1}:a]aresample=48000,aformat=channel_layouts=stereo{tempo},adelay={delay}|{delay}[n{i}]')
        labels += f'[n{i}]'
    parts.append(f'{labels}amix=inputs={len(narration)}:normalize=0:dropout_transition=0,apad[narr]')
    tail = ',loudnorm=I=-14:TP=-1.5:LRA=11' if normalize_audio else ''
    if media.get('audio_index') is not None and original_volume > 0:
        parts.append(f"[0:{media['audio_index']}]aresample=async=1:first_pts=0,aresample=48000,"
                     f"aformat=channel_layouts=stereo,volume={original_volume:.2f}[orig]")
        parts.append('[narr]asplit=2[nsc][nmix]')
        parts.append('[orig][nsc]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=400[duck]')
        parts.append(f'[duck][nmix]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95{tail}[aout]')
    else:
        parts.append(f'[narr]alimiter=limit=0.95{tail}[aout]')
    return ';'.join(parts)


def build_export_args(source: Path, media: dict, start: float, duration: float, fmt: str, framing: str,
                      subtitles_file: str | None, normalize_audio: bool, output_name: str,
                      narration: list[dict] | None = None, original_volume: float = 1.0,
                      keep: list[tuple[float, float]] | None = None,
                      track: dict | None = None) -> tuple[list[str], tuple[int, int]]:
    graph, size = build_filtergraph(media, fmt, framing, subtitles_file, keep, track)
    args = ['-ss', f'{start:.3f}', '-i', str(source)]
    for item in narration or []:
        args += ['-i', str(item['path'])]
    # "-t" depois de todas as entradas = opção de saída (duração do corte).
    args += ['-t', f'{duration:.3f}']
    if narration:
        graph += ';' + _narration_audio_graph(media, narration, original_volume, normalize_audio)
        args += ['-filter_complex', graph, '-map', '[vout]', '-map', '[aout]',
                 '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2']
    else:
        args += ['-filter_complex', graph, '-map', '[vout]']
    if narration:
        pass  # áudio já mapeado acima ([aout])
    elif media.get('audio_index') is not None:
        audio_filter = 'aresample=async=1:first_pts=0'
        if keep:
            audio_filter = f"aselect='{_select_expr(keep)}',asetpts=N/SR/TB," + audio_filter
        if normalize_audio:
            audio_filter += ',loudnorm=I=-14:TP=-1.5:LRA=11'
        args += ['-map', f"0:{media['audio_index']}", '-af', audio_filter,
                 '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-ac', '2']
    else:
        args += ['-an']
    args += ['-c:v', 'libx264', '-preset', config.FFMPEG_PRESET, '-crf', '20', '-pix_fmt', 'yuv420p',
             '-profile:v', 'high', '-movflags', '+faststart', '-avoid_negative_ts', 'make_zero',
             # Remove metadados do original (ex.: localização GPS de celulares)
             '-map_metadata', '-1', '-map_chapters', '-1', '-sn', '-dn', output_name]
    return args, size


def validate_output(path: Path, *, expected_duration: float, expected_size: tuple[int, int],
                    expect_audio: bool, cancel_event: threading.Event | None = None) -> dict:
    """Validação real do arquivo exportado. Código de saída zero não basta."""
    checks: list[dict] = []
    warnings: list[str] = []

    def check(name: str, ok: bool, detail: str):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})

    if not path.is_file() or path.stat().st_size < 1024:
        raise AppError('O FFmpeg não gerou um arquivo de vídeo válido.', 500, 'export_invalid')
    data = _ffprobe_json(path)
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    check('trilha_video', video is not None, 'Trilha de vídeo presente' if video else 'Sem trilha de vídeo')
    if video:
        size = (int(video.get('width') or 0), int(video.get('height') or 0))
        check('resolucao', size == tuple(expected_size), f'{size[0]}×{size[1]} (esperado {expected_size[0]}×{expected_size[1]})')
        check('codec', video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p',
              f"{video.get('codec_name')} / {video.get('pix_fmt')}")
        fps = _parse_rate(video.get('avg_frame_rate'))
        check('taxa_quadros', 1 <= fps <= 61, f'{fps:.2f} fps')
    duration = _parse_float((data.get('format') or {}).get('duration')) or 0
    tolerance = max(0.5, expected_duration * 0.03)
    check('duracao', abs(duration - expected_duration) <= tolerance,
          f'{duration:.2f} s (esperado {expected_duration:.2f} s ± {tolerance:.2f})')
    if expect_audio:
        check('trilha_audio', audio is not None, 'Áudio presente' if audio else 'Áudio ausente')
        if audio and video:
            a_dur = _parse_float(audio.get('duration')) or 0
            v_dur = _parse_float(video.get('duration')) or 0
            a_start = float(audio.get('start_time') or 0)
            v_start = float(video.get('start_time') or 0)
            drift = abs(a_dur - v_dur)
            offset = abs(a_start - v_start)
            check('sincronia_audio', drift <= 0.35 and offset <= 0.12,
                  f'diferença de duração {drift:.2f} s, deslocamento inicial {offset:.3f} s')
    # Decodificação completa: detecta erros de contêiner/fluxo e trechos pretos.
    try:
        stderr = run_ffmpeg(['-loglevel', 'level+info', '-i', path.name, '-vf',
                             'blackdetect=d=1.0:pix_th=0.10', '-f', 'null', '-'],
                            duration=max(duration, 0.1), cwd=path.parent, cancel_event=cancel_event)
    except FFmpegError as exc:
        stderr = exc.stderr_tail
        check('decodificacao', False, 'Falha ao decodificar o arquivo exportado')
    else:
        errors = [line for line in stderr.splitlines() if '[error]' in line or '[fatal]' in line]
        check('decodificacao', not errors, 'Sem erros de decodificação' if not errors else sanitize(errors[0], 200))
    blacks = re.findall(r'black_start:([0-9.]+)\s+black_end:([0-9.]+)', stderr)
    black_total = sum(float(b) - float(a) for a, b in blacks)
    if blacks:
        warnings.append(f'{len(blacks)} trecho(s) quase totalmente pretos ({black_total:.1f} s). '
                        'Confira se isso existe no vídeo original.')
    valid = all(c['ok'] for c in checks)
    return {'valid': valid, 'checks': checks, 'warnings': warnings, 'duration': round(duration, 3),
            'size_bytes': path.stat().st_size}


def export_clip(source: Path, media: dict, out_dir: Path, base_name: str, start: float, end: float, *,
                fmt: str = '9:16', framing: str = 'fit', ass_text: str | None = None, srt_text: str | None = None,
                normalize_audio: bool = False, on_progress: ProgressFn | None = None,
                cancel_event: threading.Event | None = None, narration: list[dict] | None = None,
                original_volume: float = 1.0, keep: list[tuple[float, float]] | None = None,
                track: dict | None = None) -> dict:
    """Renderiza em pasta temporária, valida e só então publica com renomeação atômica."""
    if fmt not in FORMATS:
        raise AppError('Formato de exportação inválido.', 400, 'bad_format')
    if framing not in FRAMINGS:
        raise AppError('Enquadramento inválido.', 400, 'bad_framing')
    end = min(end, media['duration'])
    duration = end - start
    if start < 0 or duration < 1 or duration > config.MAX_CLIP_SECONDS:
        raise AppError(f'O corte precisa durar entre 1 e {config.MAX_CLIP_SECONDS} segundos '
                       'dentro da duração do vídeo.', 400, 'bad_range')
    if keep and narration:
        keep = None  # narração usa a linha do tempo original
    if keep:
        keep = snap_intervals(keep, output_fps(media) or media.get('fps') or 30)
    out_duration = sum(b - a for a, b in keep) if keep else duration
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / '.tmp' / secrets.token_hex(8)
    work.mkdir(parents=True)
    warnings: list[str] = []
    progress = on_progress or (lambda p, m: None)
    try:
        subtitles_file = None
        if ass_text:
            (work / 'legenda.ass').write_text(ass_text, encoding='utf-8')
            subtitles_file = 'legenda.ass'
        render = 'render.mp4'

        def attempt(subs: str | None):
            args, size = build_export_args(source, media, start, duration, fmt, framing, subs,
                                           normalize_audio, render, narration, original_volume, keep, track)
            run_ffmpeg(args, duration=out_duration, cwd=work, cancel_event=cancel_event,
                       on_progress=lambda p, m: progress(p * 0.85, 'Renderizando'))
            return size

        subtitles_burned = bool(subtitles_file)
        try:
            size = attempt(subtitles_file)
        except FFmpegError as exc:
            if not subtitles_file:
                raise AppError('Falha na renderização com FFmpeg. Veja o log técnico.', 500, 'ffmpeg_failed',
                               {'technical': sanitize(exc.stderr_tail[-1500:], 1500)}) from exc
            # Legendas falharam (libass/fontes): exporta sem legendas e avisa.
            warnings.append('As legendas não puderam ser gravadas no vídeo (FFmpeg/libass). '
                            'O MP4 foi exportado sem legendas; o arquivo SRT está disponível separadamente.')
            subtitles_burned = False
            (work / render).unlink(missing_ok=True)
            try:
                size = attempt(None)
            except FFmpegError as exc2:
                raise AppError('Falha na renderização com FFmpeg, mesmo sem legendas.', 500, 'ffmpeg_failed',
                               {'technical': sanitize(exc2.stderr_tail[-1500:], 1500)}) from exc2
        progress(0.86, 'Validando arquivo exportado')
        report = validate_output(work / render, expected_duration=out_duration, expected_size=size,
                                 expect_audio=media.get('audio_index') is not None or bool(narration),
                                 cancel_event=cancel_event)
        if not report['valid']:
            failed = [c for c in report['checks'] if not c['ok']]
            raise AppError('O vídeo exportado não passou na validação: ' +
                           '; '.join(f"{c['check']}: {c['detail']}" for c in failed), 500, 'export_invalid',
                           {'validation': report})
        # Publicação atômica com nome versionado (evita arquivo travado no Windows).
        slug = FORMAT_SLUG[fmt]
        with storage.lock_for(str(out_dir.resolve())):
            version = 1 + max([int(m.group(1)) for p in out_dir.glob(f'{base_name}_{slug}_v*.mp4')
                               if (m := re.search(r'_v(\d+)\.mp4$', p.name))] or [0])
            final = out_dir / f'{base_name}_{slug}_v{version}.mp4'
            os.replace(work / render, final)
            srt_name = None
            if srt_text:
                srt_name = final.with_suffix('.srt').name
                tmp_srt = work / 'legenda.srt'
                tmp_srt.write_text(srt_text, encoding='utf-8')
                os.replace(tmp_srt, out_dir / srt_name)
        progress(1.0, 'Concluído')
        return {'file': final.name, 'srt_file': srt_name, 'format': fmt, 'framing': framing,
                'face_tracking': bool(framing == 'track' and track and track.get('ok')),
                'width': size[0], 'height': size[1], 'start': round(start, 3), 'end': round(end, 3),
                'subtitles_burned': subtitles_burned, 'normalize_audio': normalize_audio,
                'ai_narration': bool(narration),
                'shortened': ({'removed_seconds': round(duration - out_duration, 2), 'segments': len(keep)}
                              if keep else None),
                'warnings': warnings + report['warnings'], 'validation': report}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            work.parent.rmdir()  # remove a pasta .tmp se estiver vazia
        except OSError:
            pass


def ffmpeg_capabilities() -> dict:
    """Diagnóstico para a tela de saúde do sistema."""
    info = {'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe')),
            'libass': False, 'libx264': False, 'version': None}
    if not info['ffmpeg']:
        return info
    try:
        version = run([shutil.which('ffmpeg'), '-hide_banner', '-version'], timeout=15).stdout.splitlines()
        info['version'] = version[0][:120] if version else None
        filters = run([shutil.which('ffmpeg'), '-hide_banner', '-filters'], timeout=15).stdout
        info['libass'] = bool(re.search(r'^\s*\S+\s+ass\s', filters, re.M))
        encoders = run([shutil.which('ffmpeg'), '-hide_banner', '-encoders'], timeout=15).stdout
        info['libx264'] = 'libx264' in encoders
    except (subprocess.SubprocessError, OSError):
        pass
    return info
