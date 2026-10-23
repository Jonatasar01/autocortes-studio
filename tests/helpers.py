"""Geração de vídeos de teste com FFmpeg (material sintético original, sem direitos de terceiros)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

HAS_FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


def make_video(path: Path, *, size: str = '640x360', duration: float = 6, fps: int = 25, audio: bool = True,
               vcodec: str = 'libx264', acodec: str = 'aac', extra: list[str] | None = None) -> Path:
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
           '-i', f'testsrc2=s={size}:r={fps}:d={duration}']
    if audio:
        cmd += ['-f', 'lavfi', '-i', f'sine=f=440:d={duration}', '-c:a', acodec]
    cmd += ['-c:v', vcodec]
    if vcodec == 'libx264':
        cmd += ['-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
    cmd += (extra or []) + [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def fake_transcript(duration: float, phrases: list[str] | None = None) -> list[dict]:
    phrases = phrases or [
        'Você sabia que a maioria das pessoas erra isso todos os dias?',
        'O segredo está em entender o processo antes de começar.',
        'Primeiro, separe os materiais com calma e atenção.',
        'Depois, revise cada etapa antes de publicar o resultado.',
        'Agora vamos falar sobre o preço do café, que subiu muito.',
        'A seca afetou as plantações de Minas Gerais neste ano.',
    ]
    out, t, i = [], 0.4, 0
    while t + 3.2 < duration:
        text = phrases[i % len(phrases)]
        words = text.split()
        step = 3.0 / len(words)
        out.append({'start': round(t, 2), 'end': round(t + 3.0, 2), 'text': text, 'avg_logprob': -0.3,
                    'words': [{'start': round(t + k * step, 2), 'end': round(t + (k + 1) * step, 2), 'word': w}
                              for k, w in enumerate(words)]})
        t += 3.4
        i += 1
    return out
