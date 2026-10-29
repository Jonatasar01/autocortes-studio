"""Pacote de publicação e pasta de saída.

Para cada exportação validada gera um texto com título, descrição sugerida, hashtags e os avisos
que precisam ser resolvidos antes de publicar (direitos, voz por IA). Se a pasta de saída estiver
ativa, copia MP4 + SRT + texto para ``saida/<vídeo>/`` de forma atômica.

As sugestões são extraídas do próprio conteúdo (transcrição/roteiro); nada é inventado.
"""
from __future__ import annotations

import collections
import contextlib
import os
import re
import shutil
import unicodedata
from pathlib import Path

from . import config, selection


def _fold(word: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', word) if unicodedata.category(c) != 'Mn')


GENERIC = set("""tudo todos todas nada algo alguém ninguém parecia parece ficou fica estava estão estamos tinha tenho
temos quer querem saber sabe disse diz falou fala fazer feito agora hoje ontem depois antes ainda sempre nunca muito
muita muitos muitas pouco coisa coisas pessoa pessoas gente aqui ali onde quando porque então assim mesmo mesma outro
outra outros outras cada qual quais isso isto aquilo esse essa este esta vamos vai vou foram seria sobre entre normal
mudou completamente realmente simplesmente acontece aconteceu olha olhe""".split())


def hashtags(text: str, fmt: str, limit: int = 4) -> list[str]:
    words = [w for w in selection.tokens(text)
             if len(w) >= 4 and w not in selection.STOPWORDS and w not in GENERIC and not w.isdigit()]
    counts = collections.Counter(words)
    ranked = sorted(counts, key=lambda w: (-counts[w], -len(w)))[:limit]
    tags = ['#' + _fold(w) for w in ranked]
    if fmt == '9:16':
        tags.append('#shorts')
    return list(dict.fromkeys(tags))


def _summary(text: str, limit: int = 300) -> str:
    text = re.sub(r'\s+', ' ', text or '').strip()
    if len(text) <= limit:
        return text
    sentences = re.split(r'(?<=[.!?…])\s', text)
    out = ''
    for sentence in sentences:
        if len(out) + len(sentence) + 1 > limit:
            break
        out = (out + ' ' + sentence).strip()
    return out or text[:limit].rsplit(' ', 1)[0] + '…'


def package(project: dict, clip: dict, record: dict) -> str:
    title = record.get('title') or clip.get('title') or 'Corte'
    source_text = clip.get('description', '')
    description = _summary(source_text)
    tags = hashtags(source_text, record.get('format'))
    rights_info = project.get('rights') or {}
    pending = [f"- {i['label']}: {i['status_label']}" for i in rights_info.get('items', [])
               if i['status'] in ('risco_alto', 'revisar', 'nao_verificado')]
    warnings = list(dict.fromkeys(record.get('warnings') or []))
    lines = [
        'TÍTULO SUGERIDO', title, '',
        'DESCRIÇÃO SUGERIDA (revise antes de publicar)', description + ('\n\n' + ' '.join(tags) if tags else ''), '',
        'ARQUIVOS', f"- Vídeo: {record['file']} ({record['width']}×{record['height']}, {record['format']})",
    ]
    if record.get('srt_file'):
        lines.append(f"- Legendas: {record['srt_file']}")
    lines += ['', 'ANTES DE PUBLICAR']
    if pending:
        lines.append('- Pendências de direitos (não publique sem resolver):')
        lines += ['  ' + item for item in pending]
    else:
        lines.append('- Direitos declarados sem pendências. Isso não é liberação jurídica; confira as verificações da plataforma.')
    lines += ['- ' + w for w in warnings]
    lines += ['', 'Gerado localmente pelo AutoCortes Studio. Sugestões baseadas no conteúdo do corte; revise antes de usar.']
    return '\n'.join(lines) + '\n'


def write_package(clips_dir: Path, project: dict, clip: dict, record: dict) -> str:
    name = Path(record['file']).with_suffix('.txt').name
    tmp = clips_dir / (name + '.part')
    tmp.write_text(package(project, clip, record), encoding='utf-8')
    os.replace(tmp, clips_dir / name)
    return name


def _safe(name: str, limit: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', name or '').strip(' .')
    name = re.sub(r'\s+', ' ', name)
    if len(name) > limit:  # corta em fronteira de palavra
        name = name[:limit].rsplit(' ', 1)[0] if ' ' in name[:limit] else name[:limit]
    return name.strip(' .,;:') or 'video'


def output_folder(project: dict) -> Path:
    return config.OUTPUT_DIR / f"{_safe(Path(project.get('name') or 'video').stem)} ({project['id'][:6]})"


def copy_to_output(clips_dir: Path, project: dict, clip: dict, record: dict) -> list[str]:
    """Cópia atômica para a pasta de saída (arquivo .part + os.replace). Retorna os nomes copiados."""
    dest = output_folder(project)
    dest.mkdir(parents=True, exist_ok=True)
    base = f"{clip['id']:02d} - {_safe(record.get('title') or clip.get('title') or 'corte', 50)} [{record['format'].replace(':', 'x')}]"
    copied = []
    for source_name, suffix in ((record.get('file'), '.mp4'), (record.get('srt_file'), '.srt'),
                                (record.get('package_file'), ' - publicação.txt')):
        if not source_name:
            continue
        target = dest / (base + suffix)
        tmp = dest / (base + suffix + '.part')
        try:
            shutil.copyfile(clips_dir / source_name, tmp)
            os.replace(tmp, target)
            copied.append(target.name)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
    return copied
