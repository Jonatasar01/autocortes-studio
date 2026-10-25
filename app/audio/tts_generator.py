"""Narração por voz neural (Microsoft Edge TTS, biblioteca ``edge-tts``) — recurso opcional.

``synthesize(texto, destino)`` grava um ``.mp3`` com a voz pedida (padrão ``pt-BR-AntonioNeural``).

- Requer internet: o texto é enviado ao serviço de leitura em voz alta do Microsoft Edge. O
  ``edge-tts`` não é uma API oficial com contrato de serviço; pode mudar ou parar sem aviso.
- Instalação: ``pip install -r requirements-tts.txt`` (ou ``uv sync --extra tts``).
- A saída é sempre MP3 (24 kHz mono, o formato do serviço); o FFmpeg lê direto na masterização.
- Voz sintética: informe o público quando a plataforma exigir divulgação de conteúdo gerado por IA.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from ..errors import AppError, sanitize

DEFAULT_VOICE = 'pt-BR-AntonioNeural'
VOICES = ('pt-BR-AntonioNeural', 'pt-BR-FranciscaNeural', 'pt-BR-ThalitaMultilingualNeural')
MAX_CHARS = 5000            # roteiro de Shorts/Reels; textos maiores quase sempre são erro de coluna
_VOICE_RE = re.compile(r'[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural')
_RATE_RE = re.compile(r'[+-]\d{1,3}%')
AI_VOICE_NOTICE = ('A narração usa voz sintética (Microsoft Edge TTS). Informe o público quando a plataforma '
                   'exigir divulgação de conteúdo gerado ou alterado por IA.')


def available() -> bool:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


def clean_script(text: str) -> str:
    """Normaliza espaços e quebras; mantém a pontuação, que dá as pausas da voz."""
    text = re.sub(r'[ \t ]+', ' ', str(text or ''))
    text = re.sub(r'\s*\n\s*', '\n', text).strip()
    return text


def synthesize(text: str, dest: Path | str, *, voice: str = DEFAULT_VOICE, rate: str = '+0%') -> Path:
    """Gera a narração de ``text`` em ``dest`` (MP3). Levanta ``AppError`` com mensagem clara se falhar."""
    text = clean_script(text)
    if not text:
        raise AppError('O roteiro da narração está vazio.', 400, 'tts_empty')
    if len(text) > MAX_CHARS:
        raise AppError(f'O roteiro tem {len(text)} caracteres; o limite é {MAX_CHARS}.', 400, 'tts_too_long')
    if not _VOICE_RE.fullmatch(voice):
        raise AppError(f'Voz inválida: "{voice}" (exemplo: {DEFAULT_VOICE}).', 400, 'tts_voice')
    if not _RATE_RE.fullmatch(rate):
        raise AppError(f'Velocidade inválida: "{rate}" (exemplo: +10% ou -5%).', 400, 'tts_rate')
    try:
        import edge_tts
    except ImportError as exc:
        raise AppError('edge-tts não instalado: pip install -r requirements-tts.txt', 500, 'tts_missing') from exc

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Grava num temporário ao lado e só renomeia no fim: uma falha no meio não deixa MP3 truncado.
    fd, tmp = tempfile.mkstemp(prefix='.tts-', suffix='.mp3', dir=dest.parent)
    os.close(fd)
    try:
        edge_tts.Communicate(text, voice, rate=rate).save_sync(tmp)
        if Path(tmp).stat().st_size == 0:
            raise AppError('O serviço de voz não devolveu áudio. Confira a voz escolhida.', 502, 'tts_empty_audio')
        os.replace(tmp, dest)
    except AppError:
        raise
    except Exception as exc:  # rede, voz inexistente, mudança no serviço
        raise AppError('Falha ao gerar a narração (edge-tts): ' + sanitize(str(exc) or type(exc).__name__, 300),
                       502, 'tts_failed') from exc
    finally:
        Path(tmp).unlink(missing_ok=True)
    return dest
