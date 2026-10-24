"""Processamento em lote: CSV, corte bruto exato, layouts 9:16 e legendas ASS."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import media
from app.batch import captions, layouts, runner
from app.batch.ingest import CsvError, parse_csv, parse_timestamp
from app.batch.slicer import slice_copy
from tests.helpers import HAS_FFMPEG, fake_transcript, make_video


class TimestampTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_timestamp('00:15:30'), 930)
        self.assertEqual(parse_timestamp('15:30'), 930)
        self.assertEqual(parse_timestamp('930'), 930)
        self.assertEqual(parse_timestamp('00:00:12,5'), 12.5)
        self.assertEqual(parse_timestamp('01:02:03.250'), 3723.25)

    def test_invalid(self):
        for bad in ('', 'abc', '1:2:3:4', '00:61:00', '00:10.5:00', '-5'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_timestamp(bad)


class CsvTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='lote-csv-'))

    def write(self, text: str, encoding: str = 'utf-8-sig') -> Path:
        path = self.dir / 'cortes.csv'
        path.write_text(text, encoding=encoding)
        return path

    def test_semicolon_relative_paths_and_errors(self):
        result = parse_csv(self.write(
            'video_origem;tempo_inicio;tempo_fim;layout;nome\n'
            'videos/a.mp4;00:00:10;00:00:40;single_speaker;Abertura\n'
            '\n'
            'b.mp4;00:01:00;00:00:30;pip;\n'
            'c.mp4;00:00:01;00:00:09;carrossel;\n'
            'd.mp4;00:00:01;00:00:09;Split-Screen;\n'))
        self.assertEqual(len(result.rows), 2)
        first = result.rows[0]
        self.assertEqual(first.video, self.dir.resolve() / 'videos' / 'a.mp4')
        self.assertEqual((first.start, first.end, first.layout, first.name), (10, 40, 'single_speaker', 'Abertura'))
        self.assertEqual(result.rows[1].layout, 'split_screen')
        self.assertEqual(len(result.errors), 2)
        self.assertTrue(result.errors[0].startswith('Linha 4:'))
        self.assertIn('carrossel', result.errors[1])

    def test_comma_and_cp1252(self):
        result = parse_csv(self.write('video_origem,tempo_inicio,tempo_fim,layout\nvídeo.mp4,5,20,pip\n', 'cp1252'))
        self.assertEqual(result.rows[0].video.name, 'vídeo.mp4')

    def test_missing_columns(self):
        with self.assertRaises(CsvError):
            parse_csv(self.write('video,inicio,fim\na.mp4,1,2\n'))


class CaptionTests(unittest.TestCase):
    def words(self, texts, step=0.3):
        return [{'start': i * step, 'end': i * step + 0.25, 'text': t} for i, t in enumerate(texts)]

    def test_groups_respect_word_limit_and_sentence_end(self):
        texts = 'um dois três quatro cinco seis sete. oito nove'.split()
        for limit in (3, 4, 5):
            blocks = captions.group_words(self.words(texts), limit)
            self.assertTrue(all(len(b) <= limit for b in blocks), limit)
            self.assertTrue(any(b[-1]['text'] == 'sete.' for b in blocks))
        self.assertTrue(all(len(b) <= 5 for b in captions.group_words(self.words(texts), 9)))

    def test_pause_breaks_block(self):
        ws = self.words(['a', 'b', 'c'])
        ws[2]['start'], ws[2]['end'] = 3.0, 3.2
        self.assertEqual([len(b) for b in captions.group_words(ws, 5)], [2, 1])

    def test_join_words(self):
        self.assertEqual(captions.join_words(['PESSOAL,', 'BEM', '-VINDOS', 'AO']), 'PESSOAL, BEM-VINDOS AO')

    def test_ass_style_and_timing(self):
        ass = captions.build_ass(fake_transcript(12), 12, max_words=3)
        style = next(line for line in ass.splitlines() if line.startswith('Style: Retencao'))
        fields = style.split(',')
        self.assertIn('&H0000FFFF', fields[3])          # amarelo
        self.assertEqual(fields[5], '&H00000000')        # contorno preto
        self.assertEqual(fields[18], '5')                # centro da tela
        self.assertIn('PlayResX: 1080', ass)
        events = [line for line in ass.splitlines() if line.startswith('Dialogue:')]
        self.assertTrue(events)
        for line in events:
            text = line.split(',,', 2)[-1].split('}', 1)[-1]
            self.assertLessEqual(len(text.split()), 3)
            self.assertEqual(text, text.upper())

    def test_segment_without_word_times(self):
        ass = captions.build_ass([{'start': 1, 'end': 4, 'text': 'texto sem palavras com tempo'}], 10)
        self.assertEqual(len([line for line in ass.splitlines() if line.startswith('Dialogue:')]), 2)

    def test_no_speech(self):
        self.assertIsNone(captions.build_ass([], 10))


class LayoutTests(unittest.TestCase):
    def test_graphs(self):
        self.assertIn('crop=1080:1920', layouts.build('single_speaker'))
        split = layouts.build('split_screen', 'legenda.ass')
        self.assertIn('vstack', split)
        self.assertTrue(split.endswith(',ass=legenda.ass[vout]'))
        self.assertIn('boxblur', layouts.build('pip'))
        with self.assertRaises(ValueError):
            layouts.build('mosaico')


def frames_from(path: Path, at: float, count: int) -> list[str]:
    """MD5 de ``count`` quadros decodificados em sequência a partir de ``at`` (como a renderização lê)."""
    out = subprocess.run(['ffmpeg', '-loglevel', 'error', '-ss', f'{at:.6f}', '-i', str(path), '-map', '0:v:0',
                          '-frames:v', str(count), '-f', 'framemd5', '-'], capture_output=True, text=True,
                         check=True).stdout
    return [line.rsplit(',', 1)[-1].strip() for line in out.splitlines() if line and not line.startswith('#')]


@unittest.skipUnless(HAS_FFMPEG, 'FFmpeg ausente')
class BatchMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='lote-media-'))
        # GOP longo (8 s): o quadro-chave fica bem antes do início pedido.
        cls.source = make_video(cls.dir / 'fonte.mp4', size='1280x720', duration=24, fps=25,
                                extra=['-g', '200', '-keyint_min', '200', '-sc_threshold', '0'])

    def test_slice_is_frame_exact(self):
        # Início longe do quadro-chave (8 s) e logo depois dele; os quadros lidos a partir de
        # offset no temporário precisam ser exatamente os do original a partir do início pedido.
        for start, end in ((10.4, 15.0), (8.04, 9.6), (0.0, 2.0)):
            with self.subTest(start=start):
                sliced = slice_copy(self.source, start, end, self.dir / 'temp.mp4')
                count = int((end - start) * 25) - 2
                self.assertEqual(frames_from(sliced.path, sliced.offset, count),
                                 frames_from(self.source, start, count))

    def test_each_layout_renders_vertical_and_cleans_temp(self):
        out = self.dir / 'output'
        csv_path = self.dir / 'cortes.csv'
        csv_path.write_text('video_origem,tempo_inicio,tempo_fim,layout,nome\n'
                            'fonte.mp4,00:00:10.4,00:00:13,single_speaker,Um\n'
                            'fonte.mp4,00:00:02,00:00:04.5,split_screen,\n'
                            'fonte.mp4,00:00:20,00:00:30,pip,Final\n', encoding='utf-8')
        created: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*args, **kwargs):
            created.append(real_mkdtemp(*args, **kwargs))
            return created[-1]

        opts = runner.Options(output_dir=out, captions=False, face_tracking='nao')
        with mock.patch.object(runner.tempfile, 'mkdtemp', spy):
            results, skipped = runner.run_batch(csv_path, opts, log=lambda _m: None)
        self.assertEqual(skipped, [])
        self.assertTrue(all(r.ok for r in results), [r.message for r in results])
        expected = [2.6, 2.5, 4.0]  # o último foi ajustado ao fim do vídeo (24 s)
        self.assertIn('ajustado', ' '.join(results[2].warnings))
        for res, dur in zip(results, expected):
            info = media.probe_media(res.output)
            self.assertEqual((info['width'], info['height']), (1080, 1920))
            self.assertAlmostEqual(info['duration'], dur, delta=0.15)
        self.assertEqual(results[0].output.name, '001_um_single_speaker.mp4')
        self.assertEqual(len(created), 3)
        self.assertFalse(any(Path(p).exists() for p in created))

    def test_letterbox_is_detected_and_removed(self):
        # Quadro 16:9 com faixas pretas de 90 px embutidas (conteúdo 1280x540, como um filme 2.39:1).
        boxed = make_video(self.dir / 'letterbox.mp4', size='1280x540', duration=12, fps=25,
                           extra=['-vf', 'pad=1280:720:0:90:black'])
        info = media.probe_media(boxed)
        box = media.detect_letterbox(boxed, info, 2, 8)
        self.assertEqual(box, {'w': 1280, 'h': 540, 'x': 0, 'y': 90})
        self.assertIsNone(media.detect_letterbox(self.source, media.probe_media(self.source), 2, 8))
        self.assertIn('crop=1280:540:0:90,', layouts.build('pip', letterbox=box))
        graph, _ = media.build_filtergraph({**info, 'letterbox': box}, '9:16', 'blur', None)
        self.assertTrue(graph.startswith(f"[0:{info['video_index']}]crop=1280:540:0:90,"))
        original, _ = media.build_filtergraph({**info, 'letterbox': box}, 'original', 'blur', None)
        self.assertNotIn('crop=1280:540', original)

        out = self.dir / 'sem_faixas'
        row = parse_csv(self._csv('letterbox.mp4,00:00:02,00:00:06,pip\n')).rows[0]
        res = runner.process_row(row, 1, runner.Options(output_dir=out, captions=False, face_tracking='nao'),
                                 log=lambda _m: None)
        self.assertTrue(res.ok, res.message)
        self.assertIn('faixas pretas', ' '.join(res.warnings))
        # No PIP o fundo desfocado vem do conteúdo, não das faixas: o topo do quadro não é preto.
        top = subprocess.run(['ffmpeg', '-loglevel', 'error', '-ss', '1', '-i', str(res.output), '-frames:v', '1',
                              '-vf', 'crop=1080:120:0:0,signalstats,metadata=print:key=lavfi.signalstats.YAVG',
                              '-f', 'null', '-'], capture_output=True, text=True).stderr
        self.assertNotIn('YAVG=0.', top)

    def test_captions_are_burned_when_transcript_exists(self):
        opts = runner.Options(output_dir=self.dir / 'com_legenda', face_tracking='nao')
        row = parse_csv(self._csv('fonte.mp4,00:00:01,00:00:07,pip\n')).rows[0]
        with mock.patch.object(runner.transcription, 'available', return_value=True), \
                mock.patch.object(runner.captions, 'transcribe',
                                  return_value=(fake_transcript(6), {'state': 'ok'})):
            res = runner.process_row(row, 1, opts, log=lambda _m: None)
        self.assertTrue(res.ok, res.message)
        self.assertTrue(res.output.with_suffix('.ass').is_file())

    def _csv(self, line: str) -> Path:
        path = self.dir / 'um.csv'
        path.write_text('video_origem,tempo_inicio,tempo_fim,layout\n' + line, encoding='utf-8')
        return path


if __name__ == '__main__':
    unittest.main()
