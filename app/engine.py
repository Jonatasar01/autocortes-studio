"""Compatibilidade com a versão anterior.

O motor foi dividido em módulos (media, subtitles, transcription, selection, rights, pipeline).
Este arquivo mantém os nomes públicos antigos para scripts e testes existentes.
"""
from __future__ import annotations

from pathlib import Path

from .config import ROOT
from .media import probe_media, require_binary
from .rights import rights_review
from .selection import build_candidates, score_clip
from .subtitles import srt_timestamp

__all__ = ['ROOT', 'probe_media', 'require_binary', 'rights_review', 'build_candidates', 'score_clip',
           'srt_timestamp', 'export_video']


def export_video(source: Path, destination: Path, start: float, end: float, portrait: bool,
                 burn_subtitles: bool, transcript: list[dict], has_audio: bool) -> dict:
    """Interface antiga: exporta para a pasta de ``destination`` com validação e publicação atômica."""
    from . import media, subtitles
    info = media.probe_media(source)
    fmt = '9:16' if portrait else '16:9'
    ass = srt = None
    if burn_subtitles and transcript:
        width, height = media.target_size(fmt, info)
        cues = subtitles.build_cues(transcript, start, end, subtitles.style_for(width, height)['max_chars'])
        if cues:
            ass, srt = subtitles.to_ass(cues, width, height), subtitles.to_srt(cues)
    result = media.export_clip(source, info, destination.parent, destination.stem, start, end, fmt=fmt,
                               ass_text=ass, srt_text=srt)
    return {'file': result['file'], 'subtitles_burned': result['subtitles_burned'], 'srt_file': result['srt_file'],
            'validation': result['validation']}
