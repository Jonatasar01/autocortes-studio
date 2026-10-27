"""Regressões: gancho cortado nas bordas do vídeo e títulos truncados com reticências no meio da frase."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app import media, selection, subtitles

HOOK_90 = 'Estamos muito felizes por ter vivido tudo isso e queremos contar como foi essa experiência única'
HOOK_LONG_WORD = 'Veja ' + 'x' * 70 + ' agora'


# ----------------------------------------------------------------------------- gancho: texto das linhas ASS

def hook_lines(width=720, height=1280, text=HOOK_90):
    ass = subtitles.hook_only_ass(width, height, text, clip_len=5)
    return [line for line in ass.splitlines() if line.startswith('Dialogue:') and ',Hook,' in line]


def test_hook_event_uses_measured_line_breaks():
    (line,) = hook_lines()
    # As quebras são medidas na fonte real (\N) e fixadas com \q2: o libass não quebra de novo.
    assert '\\q2' in line.split(',,0,0,0,,', 1)[1].split('}', 1)[0]
    assert '\\N' in line


def test_hook_event_never_contains_an_unbreakable_run_wider_than_the_frame():
    (line,) = hook_lines(text=HOOK_LONG_WORD)
    text = line.split('}', 1)[1]
    longest = max(len(token) for token in re.split(r'[ ]|\\N', text))
    assert longest <= 24


def test_hook_event_truncates_on_a_word_boundary_without_cutting_words():
    text = ' '.join(['palavra'] * 30)  # 233 caracteres
    (line,) = hook_lines(text=text)
    shown = line.split('}', 1)[1]
    parts = shown.replace('\\N', ' ').split()
    assert len(' '.join(parts)) <= 91
    assert all(part.rstrip('…') == 'palavra' for part in parts) and shown.endswith('…')


def test_dynamic_style_uses_the_same_hook_event():
    ass = subtitles.to_ass_dynamic([], 720, 1280, hook_text=HOOK_90, clip_len=5)
    assert '\\q2' in [l for l in ass.splitlines() if ',Hook,' in l and l.startswith('Dialogue')][0]


# ----------------------------------------------------------------------------- gancho: render real com libass

pytestmark_libass = pytest.mark.skipif(
    not (media.ffmpeg_capabilities().get('ffmpeg') and media.ffmpeg_capabilities().get('libass')),
    reason='FFmpeg com libass ausente')


def render_box(tmp_path: Path, width: int, height: int, text: str):
    """Renderiza o gancho sobre cinza e devolve (x_min, x_max, y_min, y_max) da caixa branca."""
    (tmp_path / 'hook.ass').write_text(subtitles.hook_only_ass(width, height, text, clip_len=5), encoding='utf-8')
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
           f'color=c=0x808080:s={width}x{height}:d=3:r=10', '-vf', 'ass=hook.ass', '-ss', '1',
           '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-']
    raw = subprocess.run(cmd, cwd=tmp_path, capture_output=True, check=True).stdout
    assert len(raw) == width * height
    xs, ys = [], []
    for y in range(height):
        row = raw[y * width:(y + 1) * width]
        white = [x for x, value in enumerate(row) if value >= 250]
        if white:
            ys.append(y)
            xs += [white[0], white[-1]]
    assert xs, 'o gancho não apareceu no quadro'
    return min(xs), max(xs), min(ys), max(ys)


@pytestmark_libass
@pytest.mark.parametrize('width, height', [(720, 1280), (1280, 720), (1080, 1080)])
def test_rendered_hook_stays_inside_the_frame(tmp_path, width, height):
    x_min, x_max, _, _ = render_box(tmp_path, width, height, HOOK_90)
    margin = round(width * 0.03)
    assert x_min >= margin, f'gancho encosta/estoura a borda esquerda (x={x_min})'
    assert x_max <= width - 1 - margin, f'gancho encosta/estoura a borda direita (x={x_max})'


@pytestmark_libass
def test_rendered_long_hook_wraps_into_more_than_one_line(tmp_path):
    _, _, top1, bottom1 = render_box(tmp_path, 720, 1280, 'Curto')
    _, _, top2, bottom2 = render_box(tmp_path, 720, 1280, HOOK_90)
    assert (bottom2 - top2) >= 1.8 * (bottom1 - top1)


@pytestmark_libass
def test_rendered_hook_with_unbreakable_word_still_fits(tmp_path):
    x_min, x_max, _, _ = render_box(tmp_path, 720, 1280, HOOK_LONG_WORD)
    assert x_min >= round(720 * 0.03) and x_max <= 720 - 1 - round(720 * 0.03)


# ----------------------------------------------------------------------------- títulos

def test_interjection_title_takes_the_next_sentence():
    # Lives: "Porra." sozinho não diz do que o corte trata.
    assert selection._title_from_text('Porra. Tu acabou de tirar x1 com um cara.') == 'Porra. Tu acabou de tirar x1 com um cara.'
    assert selection._title_from_text('Certo.') == 'Certo.'


def test_short_sentence_is_kept_whole():
    assert selection._title_from_text('Isso é um teste. Outra frase.') == 'Isso é um teste.'


def test_sentence_up_to_the_limit_is_not_truncated():
    sentence = 'We are indeed very fortunate to have had the experiences that we have had.'
    assert 70 < len(sentence) <= 80  # antes do ajuste (limite 70) isto virava "…"
    assert selection._title_from_text(sentence + ' Mais uma frase.') == sentence


def test_long_run_on_sentence_is_cut_at_a_conjunction_without_ellipsis():
    text = ('And then we end up in the staging area near the dock and that is where we wait for a long time '
            'before anything else happens.')
    title = selection._title_from_text(text)
    assert title == 'And then we end up in the staging area near the dock'
    assert '…' not in title


def test_long_sentence_is_cut_at_a_comma_without_ellipsis():
    text = 'It has been a flawless mission so far and a very old machine, but it still performs like new today.'
    title = selection._title_from_text(text)
    assert title == 'It has been a flawless mission so far and a very old machine'


def test_portuguese_conjunction_boundary():
    text = 'Nós chegamos ao laboratório muito cedo naquela manhã fria porque o ônibus saiu antes do horário combinado.'
    title = selection._title_from_text(text)
    assert title == 'Nós chegamos ao laboratório muito cedo naquela manhã fria'


def test_no_natural_boundary_falls_back_to_a_word_with_ellipsis_but_never_ends_on_a_function_word():
    text = 'Several volunteers repainted the fence behind the old school building near the river bank yesterday'
    title = selection._title_from_text(text, limit=50)
    assert title == 'Several volunteers repainted the fence…'
    assert title[:-1].split()[-1].lower() not in selection.DANGLING_WORDS


@pytest.mark.parametrize('text', [
    'Researchers carefully measured temperature changes across seventeen different mountain valleys during winter months',
    'Pesquisadores mediram cuidadosamente as variações de temperatura em dezessete vales diferentes durante o inverno inteiro',
    'a ' * 60, 'The of the in the to the of the ' * 5])
def test_title_never_exceeds_the_limit_and_never_ends_on_a_dangling_word(text):
    title = selection._title_from_text(text)
    assert 0 < len(title) <= 81
    body = title.rstrip('…')
    assert body.split()[-1].lower().strip(',.;:') not in selection.DANGLING_WORDS or len(body.split()) <= 3


def test_title_from_empty_text_is_empty():
    assert selection._title_from_text('') == '' and selection._title_from_text('   ') == ''


def test_title_without_sentence_punctuation_is_still_returned():
    assert selection._title_from_text('texto curto sem pontuação') == 'texto curto sem pontuação'
