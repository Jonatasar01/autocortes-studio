"""Transcrição local com faster-whisper (opcional).

- Nunca envia áudio para fora do computador (o download inicial dos pesos é a única rede).
- Modelo em cache entre trabalhos; fallback GPU → CPU; progresso real pelo tempo transcrito.
- Retorna estado explícito para o pipeline nunca confundir "sem transcrição" com análise semântica.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from . import config
from .errors import Cancelled, sanitize

log = logging.getLogger('autocortes.transcription')
_cache: dict[tuple, object] = {}
_lock = threading.Lock()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _load_model(device: str):
    from faster_whisper import WhisperModel
    compute = 'int8' if device == 'cpu' else 'default'
    key = (config.WHISPER_MODEL, device, compute)
    with _lock:
        if key not in _cache:
            _cache.clear()  # mantém só um modelo em memória
            _cache[key] = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
        return _cache[key]


def transcribe(audio_path: Path, duration: float, *, on_progress: Callable[[float, str], None] | None = None,
               cancel_event: threading.Event | None = None) -> tuple[list[dict], dict]:
    if not available():
        return [], {'state': 'not_installed',
                    'message': 'Transcrição não instalada (faster-whisper). Sem transcrição, os cortes são '
                               'apenas temporais — não há análise do conteúdo falado.'}
    language = None if config.WHISPER_LANGUAGE.lower() == 'auto' else config.WHISPER_LANGUAGE
    devices = [config.WHISPER_DEVICE] + (['cpu'] if config.WHISPER_DEVICE != 'cpu' else [])
    last_error: Exception | None = None
    for device in devices:
        try:
            model = _load_model(device)
            segments_iter, info = model.transcribe(
                str(audio_path), language=language, vad_filter=True, word_timestamps=True,
                beam_size=3, condition_on_previous_text=False)
            result: list[dict] = []
            for seg in segments_iter:
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                if on_progress and duration > 0:
                    on_progress(min(1.0, float(seg.end) / duration), '')
                text = (seg.text or '').strip()
                no_speech = float(getattr(seg, 'no_speech_prob', 0) or 0)
                logprob = float(getattr(seg, 'avg_logprob', 0) or 0)
                if not text or (no_speech > 0.8 and logprob < -1.0):
                    continue  # provável alucinação em trecho sem fala
                words = [{'start': round(float(w.start), 2), 'end': round(float(w.end), 2), 'word': w.word.strip()}
                         for w in (getattr(seg, 'words', None) or []) if w.word.strip()]
                result.append({'start': round(float(seg.start), 2), 'end': round(float(seg.end), 2),
                               'text': text, 'words': words, 'avg_logprob': round(logprob, 3),
                               'no_speech_prob': round(no_speech, 3)})
            detected = getattr(info, 'language', None) or language or '?'
            return result, {'state': 'ok' if result else 'no_speech', 'model': config.WHISPER_MODEL,
                            'device': device, 'language': detected,
                            'message': (f'Transcrição local ({config.WHISPER_MODEL}, {device}, idioma {detected}).'
                                        if result else 'Nenhuma fala reconhecida no áudio.')}
        except Cancelled:
            raise
        except MemoryError as exc:
            last_error = exc
            with _lock:
                _cache.clear()
            return [], {'state': 'failed', 'message': 'Memória insuficiente para o modelo de transcrição. '
                        'Use WHISPER_MODEL=base ou tiny. Cortes temporais foram gerados.'}
        except Exception as exc:  # CUDA ausente, pesos não baixados, áudio ilegível...
            last_error = exc
            log.warning('Falha na transcrição em %s: %s', device, sanitize(exc, 300))
            with _lock:
                _cache.clear()
            continue
    return [], {'state': 'failed', 'message': 'Falha na transcrição local; foram gerados apenas cortes temporais. '
                                              'Detalhe: ' + sanitize(last_error, 200)}
