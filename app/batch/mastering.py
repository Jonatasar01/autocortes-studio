"""Masterização final de um corte: voz tratada + música de fundo + legenda ASS, em uma passagem.

Entrada: um corte já no tempo certo (por exemplo, a saída do lote), uma trilha de fundo
(BGM, opcional) e um ``.ass``. Saída: MP4 1080×1920 (H.264 + AAC 48 kHz estéreo).

Grafo montado (``-filter_complex``), com as entradas ``0`` = vídeo e ``1`` = BGM::

    [0:v:0] scale (cobrir 1080×1920) → crop 1080×1920 → setsar=1 → ass=legenda.ass → format=yuv420p  [vout]
    [0:a:0] acompressor → loudnorm I=-14:LRA=11:TP=-1 → aresample 48 kHz estéreo                     [a1]
            (os valores medidos numa leitura prévia só do áudio entram no loudnorm, que então
             aplica ganho linear e acerta o alvo; o vídeo continua codificado uma única vez)
    [1:a:0] volume=0.05 → aresample 48 kHz estéreo                                                    [a2]
    [a1][a2] amix=inputs=2:duration=first:dropout_transition=2:normalize=0 → alimiter                [aout]

Modo narrado (``tts_audio_path``), com as entradas ``0`` = vídeo, ``1`` = narração e ``2`` = BGM::

    [0:v:0] ... (igual) ... tpad (congela o último quadro se o vídeo for mais curto que a narração)  [vout]
    [1:a:0] acompressor → loudnorm I=-14:LRA=11:TP=-1 → aresample 48 kHz estéreo                     [nvoz]
    [0:a:0] volume=0.08 → aresample 48 kHz estéreo (áudio original só como ambiente)                 [norig]
    [2:a:0] volume=0.05 → aresample 48 kHz estéreo                                                    [nbgm]
    [nvoz][norig][nbgm] amix=inputs=3:duration=first:normalize=0 → alimiter                          [aout]

A duração final é a da narração (``-t``): o trecho original além dela é descartado e a música,
repetida ou cortada. A medição do loudnorm é feita só na narração.

Detalhes que evitam erros comuns:

- ``-stream_loop -1`` na BGM: uma música mais curta que o corte é repetida; ``duration=first``
  encerra a mistura junto com a voz, então o vídeo não fica mais longo.
- ``normalize=0`` no amix: sem isso o amix divide cada entrada pelo número de entradas e a voz
  normalizada em -14 LUFS cairia ~6 dB. O ``alimiter`` final garante o pico de -1 dBTP depois
  de somar a música.
- O ``.ass`` é copiado para uma pasta de trabalho e o FFmpeg roda com ``cwd`` nela, então o
  filtro recebe só ``legenda.ass``: nenhum caminho do Windows (``C:\\...``) precisa ser escapado.
- ``-map [vout] -map [aout]`` explícitos: nenhum fluxo é escolhido automaticamente.
- A saída é gravada na pasta de trabalho, validada e só então movida para o destino.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import config, media
from ..errors import AppError

ProgressCallback = Callable[[float], None]


@dataclass(frozen=True)
class MasteringSettings:
    width: int = 1080
    height: int = 1920
    bgm_volume: float = 0.05
    # Modo narrado: áudio original como ambiente, bem abaixo da voz (0,08 ≈ -22 dB).
    original_volume: float = 0.08
    loudness_lufs: float = -14.0
    loudness_range: float = 11.0
    true_peak_db: float = -1.0
    # acompressor: nivela a voz antes do loudnorm (picos e trechos baixos ficam mais próximos).
    compressor: str = 'threshold=0.089:ratio=3:attack=15:release=250:makeup=1.5'
    crf: int = 20
    preset: str = config.FFMPEG_PRESET
    audio_bitrate: str = '192k'
    # Mede a voz antes (só áudio, rápido) para o loudnorm atingir o alvo com precisão.
    measure_loudness: bool = True


class MasteringError(RuntimeError):
    """Falha na masterização, com o final do log do FFmpeg para diagnóstico."""

    def __init__(self, message: str, ffmpeg_log: str = ''):
        super().__init__(message + (f'\n--- log do FFmpeg ---\n{ffmpeg_log}' if ffmpeg_log else ''))
        self.ffmpeg_log = ffmpeg_log


def console_progress(fraction: float) -> None:
    """Barra de progresso simples no terminal (stderr); só redesenha quando o valor muda."""
    step = int(fraction * 1000)
    if step == getattr(console_progress, '_last', -1):
        return
    console_progress._last = step
    filled = int(fraction * 30)
    sys.stderr.write(f'\r  masterizando [{"#" * filled}{"." * (30 - filled)}] {fraction * 100:5.1f}%')
    if fraction >= 1:
        sys.stderr.write('\n')
    sys.stderr.flush()


def tpad(seconds: float) -> str:
    """Congela o último quadro por ``seconds`` (vídeo mais curto que a narração); vazio se não precisa."""
    return f',tpad=stop_mode=clone:stop_duration={seconds:.3f}' if seconds > 0.01 else ''


def audio_duration(path: Path, label: str = 'O áudio') -> float:
    """Duração de um arquivo de áudio (MP3/WAV/...); ``MasteringError`` se não houver trilha de áudio."""
    try:
        data = media._ffprobe_json(path)
    except AppError as exc:
        raise MasteringError(f'{label} não pôde ser lido: {path}') from exc
    streams = [st for st in data.get('streams') or [] if st.get('codec_type') == 'audio']
    if not streams:
        raise MasteringError(f'{label} não tem trilha de áudio: {path}')
    try:
        value = float((data.get('format') or {}).get('duration') or streams[0].get('duration'))
    except (TypeError, ValueError) as exc:
        raise MasteringError(f'{label} não tem duração legível: {path}') from exc
    if value <= 0.05:
        raise MasteringError(f'{label} está vazio: {path}')
    return value


def captions_from_narration(tts_audio_path: Path, work: Path, *, max_words: int = 4, font: str = 'Arial Black',
                            uppercase: bool = True, cancel_event: threading.Event | None = None
                            ) -> tuple[str | None, str]:
    """Legenda ASS gerada transcrevendo SÓ a narração (as falas do vídeo original ficam de fora).

    Devolve ``(texto_ass, mensagem)``; ``texto_ass`` é ``None`` se nada foi reconhecido.
    """
    from . import captions
    duration = audio_duration(Path(tts_audio_path), 'A narração')
    wav = captions.extract_audio(Path(tts_audio_path), 0.0, duration, Path(work) / 'narracao.wav',
                                 cancel_event=cancel_event)
    transcript, status = captions.transcribe(wav, duration, cancel_event=cancel_event)
    ass = captions.build_ass(transcript, duration, max_words=max_words, font=font, uppercase=uppercase)
    return ass, status.get('message', '')


class AudioVisualMastering:
    def __init__(self, settings: MasteringSettings | None = None) -> None:
        self.settings = settings or MasteringSettings()

    # ------------------------------------------------------------------ grafo

    def video_chain(self, subtitles_file: str | None, pad: float = 0.0) -> str:
        s = self.settings
        subs = f',ass={subtitles_file}' if subtitles_file else ''
        return (f'[0:v:0]scale={s.width}:{s.height}:force_original_aspect_ratio=increase:flags=bicubic,'
                f'crop={s.width}:{s.height},setsar=1{tpad(pad)}{subs},format=yuv420p[vout]')

    def _loudnorm(self, measured: dict | None = None) -> str:
        s = self.settings
        base = f'loudnorm=I={s.loudness_lufs:g}:LRA={s.loudness_range:g}:TP={s.true_peak_db:g}'
        if not measured:
            return base
        return (base + f":measured_I={measured['input_i']}:measured_LRA={measured['input_lra']}"
                f":measured_TP={measured['input_tp']}:measured_thresh={measured['input_thresh']}"
                f":offset={measured['target_offset']}:linear=true")

    def measure(self, video_in: Path, cancel_event: threading.Event | None = None,
                duration: float = 60.0) -> dict | None:
        """1ª etapa do loudnorm: mede a voz já comprimida. ``None`` se a medição falhar."""
        s = self.settings
        try:
            stderr = media.run_ffmpeg(['-loglevel', 'level+info', '-i', str(video_in), '-map', '0:a:0', '-vn',
                                       '-af', f'acompressor={s.compressor},{self._loudnorm()}:print_format=json',
                                       '-f', 'null', '-'], duration=duration, cancel_event=cancel_event)
        except media.FFmpegError:
            return None
        start, end = stderr.rfind('{'), stderr.rfind('}')
        try:
            data = json.loads(stderr[start:end + 1].replace('[info]', ''))
            float(data['input_i'])
        except (ValueError, KeyError):
            return None
        # Áudio quase mudo (-inf) não tem o que normalizar: usa o modo dinâmico.
        return data if float(data['input_i']) > -70 else None

    def audio_chain(self, with_bgm: bool, measured: dict | None = None) -> str:
        s = self.settings
        voice = (f'[0:a:0]acompressor={s.compressor},{self._loudnorm(measured)},'
                 f'aresample=48000,aformat=channel_layouts=stereo')
        # 0,5 dB abaixo do alvo: a codificação AAC pode subir um pouco os picos.
        peak = 10 ** ((s.true_peak_db - 0.5) / 20)
        if not with_bgm:
            return f'{voice},alimiter=limit={peak:.3f}:level=0[aout]'
        return (f'{voice}[a1];'
                f'[1:a:0]volume={s.bgm_volume:g},aresample=48000,aformat=channel_layouts=stereo[a2];'
                f'[a1][a2]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,'
                f'alimiter=limit={peak:.3f}:level=0[aout]')

    def narrated_audio_chain(self, voice: str, original: str | None, bgm: str | None,
                             measured: dict | None = None) -> str:
        """Narração como faixa principal; ``original`` e ``bgm`` (rótulos de entrada) entram baixos ao fundo.

        Os rótulos são parâmetros para o lote reaproveitar a cadeia com a sua própria ordem de entradas.
        """
        s = self.settings
        fmt = 'aresample=48000,aformat=channel_layouts=stereo'
        peak = 10 ** ((s.true_peak_db - 0.5) / 20)
        limiter = f'alimiter=limit={peak:.3f}:level=0[aout]'
        voice_chain = f'{voice}acompressor={s.compressor},{self._loudnorm(measured)},{fmt}'
        beds = []
        if original:
            beds.append(f'{original}aresample=async=1:first_pts=0,volume={s.original_volume:g},{fmt}[norig]')
        if bgm:
            beds.append(f'{bgm}volume={s.bgm_volume:g},{fmt}[nbgm]')
        if not beds:
            return f'{voice_chain},{limiter}'
        labels = '[nvoz]' + ('[norig]' if original else '') + ('[nbgm]' if bgm else '')
        # duration=first: a mistura termina com a narração; normalize=0 mantém a voz em -14 LUFS.
        return ';'.join([f'{voice_chain}[nvoz]', *beds,
                         f'{labels}amix=inputs={1 + len(beds)}:duration=first:dropout_transition=0:normalize=0,'
                         + limiter])

    def _encode_args(self) -> list[str]:
        s = self.settings
        return ['-map', '[vout]', '-map', '[aout]',
                '-c:v', 'libx264', '-preset', s.preset, '-crf', str(s.crf), '-profile:v', 'high',
                '-c:a', 'aac', '-b:a', s.audio_bitrate, '-ar', '48000', '-ac', '2']

    def build_args(self, video_in: Path, bgm_in: Path | None, subtitles_file: str | None,
                   output_name: str, measured: dict | None = None, *, tts_in: Path | None = None,
                   duration: float | None = None, pad: float = 0.0, original_audio: bool = True) -> list[str]:
        """``tts_in``: modo narrado (entradas 0 = vídeo, 1 = narração, 2 = BGM), com ``duration`` = narração."""
        tail = ['-movflags', '+faststart', '-map_metadata', '-1', '-sn', '-dn', output_name]
        if tts_in is None:
            args = ['-i', str(video_in)]
            if bgm_in is not None:
                args += ['-stream_loop', '-1', '-i', str(bgm_in)]
            graph = self.video_chain(subtitles_file) + ';' + self.audio_chain(bgm_in is not None, measured)
            return args + ['-filter_complex', graph, *self._encode_args(), '-shortest', *tail]
        if not duration or duration <= 0:
            raise ValueError('modo narrado precisa da duração da narração')
        args = ['-i', str(video_in), '-i', str(tts_in)]
        if bgm_in is not None:
            args += ['-stream_loop', '-1', '-i', str(bgm_in)]
        audio = self.narrated_audio_chain('[1:a:0]', '[0:a:0]' if original_audio else None,
                                          '[2:a:0]' if bgm_in is not None else None, measured)
        graph = self.video_chain(subtitles_file, pad) + ';' + audio
        return args + ['-filter_complex', graph, *self._encode_args(), '-t', f'{duration:.3f}', *tail]

    # ------------------------------------------------------------------ execução

    def render_final_clip(self, video_in: Path | str, bgm_in: Path | str | None, subtitle_ass: Path | str | None,
                          video_out: Path | str, *, tts_audio_path: Path | str | None = None,
                          on_progress: ProgressCallback | None = None,
                          cancel_event: threading.Event | None = None) -> Path:
        """``tts_audio_path``: narração que vira a faixa principal e define a duração final.

        A legenda do modo narrado deve vir da narração (veja ``captions_from_narration``).
        """
        video_in, video_out = Path(video_in).resolve(), Path(video_out).resolve()
        bgm = Path(bgm_in).resolve() if bgm_in else None
        ass = Path(subtitle_ass).resolve() if subtitle_ass else None
        tts = Path(tts_audio_path).resolve() if tts_audio_path else None
        for label, path in (('vídeo', video_in), ('música de fundo', bgm), ('legenda', ass), ('narração', tts)):
            if path is not None and not path.is_file():
                raise MasteringError(f'Arquivo de {label} não encontrado: {path}')
        try:
            info = media.probe_media(video_in)
        except AppError as exc:
            raise MasteringError(f'Vídeo inválido: {exc.message}') from exc
        if tts is None and info.get('audio_index') is None:
            raise MasteringError('O vídeo não tem trilha de áudio; a cadeia de voz precisa dela.')
        if bgm is not None:
            audio_duration(bgm, 'A música de fundo')
        duration, pad = info['duration'], 0.0
        if tts is not None:
            duration = audio_duration(tts, 'A narração')
            pad = max(0.0, duration - info['duration'])

        work = Path(tempfile.mkdtemp(prefix='autocortes-master-'))
        try:
            subtitles_file = None
            if ass is not None:
                shutil.copyfile(ass, work / 'legenda.ass')
                subtitles_file = 'legenda.ass'
            measured = (self.measure(tts or video_in, cancel_event, duration)
                        if self.settings.measure_loudness else None)
            args = self.build_args(video_in, bgm, subtitles_file, 'master.mp4', measured, tts_in=tts,
                                   duration=duration, pad=pad, original_audio=info.get('audio_index') is not None)
            progress = (lambda f, _m='': on_progress(f)) if on_progress else None
            try:
                media.run_ffmpeg(args, duration=duration, cwd=work, on_progress=progress,
                                 cancel_event=cancel_event)
            except media.FFmpegError as exc:
                raise MasteringError(str(exc), exc.stderr_tail[-4000:]) from exc
            report = media.validate_output(work / 'master.mp4', expected_duration=duration,
                                           expected_size=(self.settings.width, self.settings.height),
                                           expect_audio=True, cancel_event=cancel_event)
            if not report['valid']:
                failed = '; '.join(f"{c['check']}: {c['detail']}" for c in report['checks'] if not c['ok'])
                raise MasteringError('O arquivo masterizado não passou na validação: ' + failed)
            video_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(work / 'master.mp4'), video_out)
            return video_out
        finally:
            media._remove_work_dir(work)
