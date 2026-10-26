"""Transcrição local com faster-whisper (opcional).

- Nunca envia áudio para fora do computador (o download inicial dos pesos é a única rede).
- Modelo em cache entre trabalhos; fallback GPU → CPU; progresso real pelo tempo transcrito.
- Retorna estado explícito para o pipeline nunca confundir "sem transcrição" com análise semântica.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Callable

from . import config
from .errors import Cancelled, sanitize

log = logging.getLogger('autocortes.transcription')
_cache: dict[tuple, object] = {}
_lock = threading.Lock()
# Depois que a GPU falha uma vez (ex.: placa NVIDIA sem as DLLs do CUDA), o processo usa só a CPU:
# tentar o CUDA de novo após a falha pode travar a transcrição seguinte indefinidamente.
_gpu_failed = False
_dll_dirs_done = False


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _cuda_dll_dirs() -> None:
    """Registra as DLLs do CUDA instaladas pelo pip (``requirements-gpu.txt``) para o CTranslate2.

    No Windows, ``nvidia-cublas-cu12`` e ``nvidia-cudnn-cu12`` ficam em ``site-packages/nvidia/*/bin``,
    fora do PATH; sem isso a GPU falha com "cublas64_12.dll is not found" e tudo roda na CPU.
    """
    global _dll_dirs_done
    if _dll_dirs_done or os.name != 'nt':
        return
    _dll_dirs_done = True
    try:
        import nvidia
    except ImportError:
        return
    for base in getattr(nvidia, '__path__', []):
        for bin_dir in sorted(Path(base).glob('*/bin')):
            os.add_dll_directory(str(bin_dir))
            os.environ['PATH'] = str(bin_dir) + os.pathsep + os.environ.get('PATH', '')


def _load_model(device: str):
    from faster_whisper import WhisperModel
    if device != 'cpu':
        _cuda_dll_dirs()
    compute = 'int8' if device == 'cpu' else 'default'
    key = (config.WHISPER_MODEL, device, compute)
    with _lock:
        if key not in _cache:
            _cache.clear()  # mantém só um modelo em memória
            _cache[key] = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
        return _cache[key]


CHUNK_SECONDS = 20 * 60      # áudio acima disto é transcrito em partes (memória fixa, ~0,5 GB por parte)
CHUNK_SEARCH_SECONDS = 30.0  # cada parte termina no ponto mais silencioso dos seus últimos 30 s
_QUIET_WINDOW = 0.1


def _quiet_cut(block, rate: int) -> int:
    """Índice do ponto mais silencioso nos últimos ``CHUNK_SEARCH_SECONDS`` (não corta uma palavra ao meio)."""
    import numpy as np
    win = int(rate * _QUIET_WINDOW)
    # só na metade final: uma parte que começa num silêncio não pode ser cortada logo no início
    search = min(len(block) // 2, int(rate * CHUNK_SEARCH_SECONDS)) // win * win
    if search <= 0:
        return len(block)
    tail = block[len(block) - search:].astype(np.float32).reshape(-1, win)
    quietest = int(np.argmin(np.mean(tail * tail, axis=1)))
    return len(block) - search + quietest * win + win // 2


def _wav_chunks(path: Path, chunk_seconds: float | None = None):
    """Lê o WAV (PCM 16 bits mono) em partes: ``(início_em_segundos, amostras float32)``."""
    import wave

    import numpy as np
    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError('áudio de transcrição precisa ser WAV PCM 16 bits mono')
        rate = wav.getframerate()
        size = int((chunk_seconds or CHUNK_SECONDS) * rate)
        offset = 0
        block = np.zeros(0, dtype='<i2')
        while True:
            need = size - len(block)
            data = np.frombuffer(wav.readframes(need), dtype='<i2')
            block = np.concatenate([block, data])
            if len(data) < need:   # fim do arquivo
                if len(block):
                    yield offset / rate, block.astype(np.float32) / 32768.0
                return
            cut = _quiet_cut(block, rate)
            yield offset / rate, block[:cut].astype(np.float32) / 32768.0
            offset += cut
            block = block[cut:]


GPU_BATCH_SIZE = max(0, int(os.getenv('WHISPER_BATCH_SIZE', '8') or 0))   # 0 desliga o lote na GPU
SENTENCE_MAX_SECONDS = 12.0
SENTENCE_PAUSE = 0.8


def _batched_pipeline(model):
    from faster_whisper import BatchedInferencePipeline
    return BatchedInferencePipeline(model)


def split_sentences(words: list[dict]) -> list[tuple[float, float, str, list[dict]]]:
    """Divide palavras com tempo em frases: pontuação final, pausa de 0,8 s ou no máximo 12 s."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current and (w['start'] - current[-1]['end'] > SENTENCE_PAUSE
                        or w['end'] - current[0]['start'] > SENTENCE_MAX_SECONDS):
            groups.append(current)
            current = []
        current.append(w)
        if w['word'].endswith(('.', '?', '!', '…')) and current[-1]['end'] - current[0]['start'] >= 1.0:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [(g[0]['start'], g[-1]['end'], ' '.join(w['word'] for w in g), g) for g in groups]


def transcribe(audio_path: Path, duration: float, *, on_progress: Callable[[float, str], None] | None = None,
               cancel_event: threading.Event | None = None) -> tuple[list[dict], dict]:
    global _gpu_failed
    if not available():
        return [], {'state': 'not_installed',
                    'message': 'Transcrição não instalada (faster-whisper). Sem transcrição, os cortes são '
                               'apenas temporais — não há análise do conteúdo falado.'}
    language = None if config.WHISPER_LANGUAGE.lower() == 'auto' else config.WHISPER_LANGUAGE
    devices = ['cpu'] if config.WHISPER_DEVICE == 'cpu' or _gpu_failed else [config.WHISPER_DEVICE, 'cpu']
    last_error: Exception | None = None
    chunked = duration > CHUNK_SECONDS
    for device in devices:
        try:
            model = _load_model(device)
            # Na GPU, inferência em lote (~2x mais rápida numa GTX 1650). Ela devolve trechos de ~30 s,
            # redivididos em frases abaixo para a seleção de cortes continuar trabalhando por frase.
            batched = device != 'cpu' and GPU_BATCH_SIZE > 0
            runner = _batched_pipeline(model) if batched else model
            lang = language
            detected = None
            result: list[dict] = []
            # Áudio longo em partes: o faster-whisper monta o espectrograma do áudio inteiro de uma vez
            # (mais de 4 GB para 6 h de fala), e a transcrição falhava por falta de memória.
            pieces = _wav_chunks(Path(audio_path)) if chunked else [(0.0, str(audio_path))]
            for offset, audio in pieces:
                extra = {'batch_size': GPU_BATCH_SIZE} if batched else {'condition_on_previous_text': False}
                segments_iter, info = runner.transcribe(
                    audio, language=lang, vad_filter=True, word_timestamps=True, beam_size=3, **extra)
                detected = detected or getattr(info, 'language', None)
                if lang is None and chunked and detected:
                    lang = detected   # o idioma detectado na 1ª parte vale para as seguintes
                for seg in segments_iter:
                    if cancel_event is not None and cancel_event.is_set():
                        raise Cancelled()
                    if on_progress and duration > 0:
                        on_progress(min(1.0, (offset + float(seg.end)) / duration), '')
                    text = (seg.text or '').strip()
                    no_speech = float(getattr(seg, 'no_speech_prob', 0) or 0)
                    logprob = float(getattr(seg, 'avg_logprob', 0) or 0)
                    if not text or (no_speech > 0.8 and logprob < -1.0):
                        continue  # provável alucinação em trecho sem fala
                    words = [{'start': round(offset + float(w.start), 2), 'end': round(offset + float(w.end), 2),
                              'word': w.word.strip()}
                             for w in (getattr(seg, 'words', None) or []) if w.word.strip()]
                    start, end = round(offset + float(seg.start), 2), round(offset + float(seg.end), 2)
                    parts = split_sentences(words) if batched and words else [(start, end, text, words)]
                    for p_start, p_end, p_text, p_words in parts:
                        result.append({'start': p_start, 'end': p_end, 'text': p_text, 'words': p_words,
                                       'avg_logprob': round(logprob, 3), 'no_speech_prob': round(no_speech, 3)})
            detected = detected or language or '?'
            return result, {'state': 'ok' if result else 'no_speech', 'model': config.WHISPER_MODEL,
                            'device': device, 'language': detected,
                            'message': (f'Transcrição local ({config.WHISPER_MODEL}, {device}, idioma {detected}).'
                                        if result else 'Nenhuma fala reconhecida no áudio.')}
        except Cancelled:
            raise
        except MemoryError as exc:
            last_error = exc
            log.warning('Memória insuficiente na transcrição em %s (%.1f h de áudio).', device, duration / 3600)
            with _lock:
                _cache.clear()
            return [], {'state': 'failed', 'message': 'Memória insuficiente para o modelo de transcrição. '
                        'Use WHISPER_MODEL=base ou tiny. Cortes temporais foram gerados.'}
        except Exception as exc:  # CUDA ausente, pesos não baixados, áudio ilegível...
            last_error = exc
            log.warning('Falha na transcrição em %s: %s', device, sanitize(exc, 300))
            if device != 'cpu':
                _gpu_failed = True
                log.warning('GPU indisponível para a transcrição; as próximas usarão só a CPU.')
            with _lock:
                _cache.clear()
            continue
    return [], {'state': 'failed', 'message': 'Falha na transcrição local; foram gerados apenas cortes temporais. '
                                              'Detalhe: ' + sanitize(last_error, 200)}
