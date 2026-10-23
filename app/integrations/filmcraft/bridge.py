from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from fractions import Fraction
import shutil
import subprocess
import threading
import time
from decimal import Decimal, ROUND_HALF_UP

TICKS = 254_016_000_000
SUPPORTED_VERSION = '0.12.0'
REQUIRED = {'file.newSequence', 'timeline.place', 'captions.import', 'file.exportMedia'}
# Etapa opcional de remoção de pausas (transcript.set + transcript.removePauses).
PAUSE_REQUIRED = {'transcript.set', 'transcript.removePauses', 'transcript.createCaptions', 'captions.export'}
PAUSE_DEFAULTS = {'min_seconds': 0.7, 'keep_seconds': 0.25}
MAX_PAUSE_WORDS = 200_000


class BridgeError(RuntimeError):
    pass


def normalize_pause_options(options) -> dict | None:
    """``None``/``False`` desliga a etapa; ``True`` ou um dict a liga com limites validados."""
    if options is None or options is False:
        return None
    if options is True:
        options = {}
    if not isinstance(options, dict):
        raise BridgeError('Opções de remoção de pausas inválidas.')
    unknown = set(options) - set(PAUSE_DEFAULTS)
    if unknown:
        raise BridgeError('Opção de remoção de pausas desconhecida: ' + ', '.join(sorted(map(str, unknown))))
    merged = {**PAUSE_DEFAULTS, **options}
    for key, value in merged.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BridgeError(f'Remoção de pausas: {key} deve ser um número finito.')
    minimum, keep = float(merged['min_seconds']), float(merged['keep_seconds'])
    if not 0.3 <= minimum <= 5:
        raise BridgeError('Remoção de pausas: a pausa mínima deve ficar entre 0,3 e 5 s.')
    if not 0 <= keep <= 1:
        raise BridgeError('Remoção de pausas: o silêncio mantido deve ficar entre 0 e 1 s.')
    if keep * 2 >= minimum:
        raise BridgeError('Remoção de pausas: o silêncio mantido (nos dois lados) deve ser menor que a pausa mínima.')
    return {'min_seconds': minimum, 'keep_seconds': keep}


def transcript_payload(clips: list[dict], language: str | None) -> dict:
    """Palavras dos cortes, em tempo de mídia (ticks), no formato de ``transcript.set``.

    Palavras sobrepostas (cortes que repetem o mesmo trecho da fonte) entram uma só vez.
    """
    words = []
    for clip in clips:
        for word in clip.get('words') or []:
            if not isinstance(word, dict):
                raise BridgeError('Palavra de transcrição inválida.')
            start, end, text = word.get('start_ms'), word.get('end_ms'), str(word.get('text') or '').strip()
            if any(type(v) is not int or v < 0 for v in (start, end)) or end <= start:
                raise BridgeError('Tempos de palavra inválidos (inteiros em ms, fim depois do início).')
            if text:
                words.append((start, end, text))
    if not words:
        raise BridgeError('Nenhuma palavra com tempo nos cortes: a remoção de pausas exige transcrição por palavra.')
    if len(words) > MAX_PAUSE_WORDS:
        raise BridgeError('Transcrição longa demais para a remoção de pausas.')
    words.sort()
    unit = TICKS // 1000
    out, last_end = [], -1
    for start, end, text in words:
        if start < last_end:
            continue
        out.append({'text': text, 'start': start * unit, 'end': end * unit})
        last_end = end
    return {'language': str(language or 'und')[:16], 'speakers': [], 'words': out}


def snap_ms(milliseconds: int, fps: Fraction) -> int:
    if type(milliseconds) is not int or milliseconds < 0:
        raise BridgeError('Tempos devem ser inteiros não negativos em milissegundos.')
    frames = Fraction(milliseconds, 1000) * fps
    frame = (frames.numerator * 2 + frames.denominator) // (2 * frames.denominator)
    ticks = frame * Fraction(TICKS, 1) / fps
    if ticks.denominator != 1:
        raise BridgeError('FPS não representável exatamente na base de ticks.')
    return int(ticks)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class FilmCraftBridge:
    def __init__(self, binary: str | Path | None = None, *, timeout=180, cancel_event=None):
        candidate = binary or os.getenv('FILMCRAFT_CLI') or shutil.which('filmcraft-cli')
        if not candidate and os.name == 'nt':
            candidate = Path(os.getenv('ProgramFiles', 'C:/Program Files'))/'FilmCraft/filmcraft-cli.exe'
        self.binary = Path(candidate).resolve() if candidate else None
        self.timeout = timeout
        self.cancel_event = cancel_event or threading.Event()
        self.log = []

    def _run(self, args, *, program=None):
        if self.cancel_event.is_set():
            raise BridgeError('Operação FilmCraft cancelada.')
        executable = str(program or self.binary)
        if not program and (self.binary is None or not self.binary.is_file()):
            raise BridgeError('FilmCraft CLI não instalado. A exportação FFmpeg continua disponível.')
        started = time.monotonic()
        process = subprocess.Popen([executable, *map(str, args)], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            while True:
                if self.cancel_event.is_set():
                    raise BridgeError('Operação FilmCraft cancelada.')
                if time.monotonic() - started > self.timeout:
                    raise BridgeError('Tempo limite do FilmCraft excedido.')
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            process.kill()
            stdout, stderr = process.communicate()
            self.log.append({'args': list(map(str, args)), 'returncode': process.returncode,
                             'stdout': stdout, 'stderr': stderr, 'interrupted': True})
            raise
        self.log.append({'args': list(map(str, args)), 'returncode': process.returncode,
                         'seconds': round(time.monotonic()-started, 3), 'stdout': stdout, 'stderr': stderr})
        if process.returncode:
            raise BridgeError(f'Falha do processo ({process.returncode}): {stderr[-1500:]}')
        return stdout

    def diagnose(self, extra_required=()):
        version = self._run(['--version']).strip()
        if version != f'filmcraft-cli {SUPPORTED_VERSION}':
            raise BridgeError(f'Versão ainda não validada: {version}. Esperado {SUPPORTED_VERSION}.')
        commands = json.loads(self._run(['commands', '--json']))
        missing = (REQUIRED | set(extra_required)) - {item['id'] for item in commands}
        if missing:
            raise BridgeError('Comandos ausentes: ' + ', '.join(sorted(missing)))
        return {'version': version, 'binary': str(self.binary), 'sha256': sha256(self.binary),
                'commands': commands, 'platform': os.name}

    def decision_from_project(self, project_id, clip_ids=None, pause_removal=None):
        """Manifesto ``autocortes-edit-v1``. Com ``pause_removal`` (ver ``normalize_pause_options``)
        inclui as palavras da transcrição de cada corte e as opções da etapa opcional."""
        from ... import storage, subtitles
        pause = normalize_pause_options(pause_removal)
        project = storage.read_project(project_id)
        if project.get('status') != 'ready':
            raise BridgeError('A análise do projeto ainda não está pronta.')
        folder = storage.project_dir(project_id).resolve()
        source = (folder/project['filename']).resolve(strict=True)
        if source.parent != folder:
            raise BridgeError('Mídia fora da pasta do projeto.')
        probe = json.loads(self._run(['-v', 'error', '-show_streams', '-of', 'json', source],
                                    program=shutil.which('ffprobe') or 'ffprobe'))
        video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        fps = Fraction(video['avg_frame_rate'])
        rate = {'num': fps.numerator, 'den': fps.denominator}
        clips = project.get('clips') or []
        if clip_ids:
            selected = {c['id']: c for c in clips}
            if any(i not in selected for i in clip_ids) or len(set(clip_ids)) != len(clip_ids):
                raise BridgeError('Cortes solicitados inválidos.')
            clips = [selected[i] for i in clip_ids]
        def ms(value):
            return int((Decimal(str(value))*1000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

        def clip_words(start_ms, end_ms):
            """Palavras (tempo da fonte, em ms) que cabem inteiras no corte."""
            found = []
            for segment in project.get('transcript') or []:
                for word in segment.get('words') or []:
                    begin, finish = ms(word['start']), ms(word['end'])
                    if start_ms <= begin and finish <= end_ms and finish > begin and str(word.get('word') or '').strip():
                        found.append({'text': str(word['word']).strip(), 'start_ms': begin, 'end_ms': finish})
            return found
        entries = []
        for c in clips:
            entry = {'id': f"clip-{c['id']:03d}", 'revision': c.get('revision', 1),
                     'source_start_ms': ms(c['start']), 'source_end_ms': ms(c['end']),
                     'title': c['title'], 'score': c.get('score'), 'framing': 'fit',
                     'captions': [{'start_ms': ms(cue['start']), 'end_ms': ms(cue['end']), 'text': cue['text']}
                                  for cue in subtitles.build_cues(project.get('transcript') or [], c['start'], c['end'], 38)]}
            if pause:
                entry['words'] = clip_words(entry['source_start_ms'], entry['source_end_ms'])
            entries.append(entry)
        manifest = {'schema_version': 'autocortes-edit-v1', 'origin_project_id': project_id,
                    'rights': project.get('rights'), 'caption_mode': 'transcript',
                    'source': {'path': str(source), 'sha256': sha256(source), 'fps': rate},
                    'clips': entries,
                    'requested_output': {'width': video['width'], 'height': video['height'], 'fps': rate}}
        if pause:
            language = (project.get('transcription') or {}).get('language')
            manifest['transcript_language'] = language if language and language != '?' else None
            manifest['pause_removal'] = pause
        return manifest

    def build(self, manifest: dict, output: Path):
        """Cria projeto em pasta nova; não modifica projetos externos existentes."""
        if manifest.get('schema_version') != 'autocortes-edit-v1':
            raise BridgeError('Versão de contrato não suportada.')
        source_name = manifest['source']['path']
        if source_name.startswith(('\\\\', '//')) or '://' in source_name:
            raise BridgeError('Use uma mídia em disco local, sem URL ou caminho de rede.')
        source = Path(source_name).resolve(strict=True)
        if not source.is_file():
            raise BridgeError('Mídia local inválida.')
        source_hash = sha256(source)
        if source_hash != manifest['source']['sha256']:
            raise BridgeError('A mídia mudou desde a criação do manifesto.')
        spec = manifest['requested_output']
        fps = Fraction(spec['fps']['num'], spec['fps']['den'])
        source_fps = Fraction(manifest['source']['fps']['num'], manifest['source']['fps']['den'])
        if source_fps != fps:
            raise BridgeError('O protótipo requer FPS de saída igual ao da fonte.')
        if fps <= 0 or fps > 120 or any(type(spec[k]) is not int or not 16 <= spec[k] <= 4096
                                       for k in ('width', 'height')):
            raise BridgeError('Resolução/FPS inválidos.')
        clips = manifest['clips']
        if not clips or len(clips) > 100 or len({c['id'] for c in clips}) != len(clips):
            raise BridgeError('Lista de cortes inválida ou IDs repetidos.')
        if any(c.get('framing', 'fit') != 'fit' for c in clips):
            raise BridgeError('Este protótipo suporta apenas fit; use FFmpeg para blur/track.')
        timings = [(snap_ms(c['source_start_ms'], fps), snap_ms(c['source_end_ms'], fps)) for c in clips]
        if any(end <= start for start, end in timings):
            raise BridgeError('Corte vazio após ajuste aos fotogramas.')
        pause = normalize_pause_options(manifest.get('pause_removal'))
        words = transcript_payload(clips, manifest.get('transcript_language')) if pause else None
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=False)
        try:
            diagnosis = self.diagnose(PAUSE_REQUIRED if pause else ())
            self._write(output/'diagnostic.json', diagnosis)
            self._write(output/'edit-decision.json', manifest)
            probe = json.loads(self._run(['-v', 'error', '-show_streams', '-show_format', '-of', 'json', source],
                                        program=shutil.which('ffprobe') or 'ffprobe'))
            duration = Fraction(probe['format']['duration'])
            video = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video or Fraction(video['avg_frame_rate']) != fps or Fraction(video['r_frame_rate']) != fps:
                raise BridgeError('Fonte sem vídeo CFR compatível com o FPS declarado.')
            if any(Fraction(end, TICKS) > duration for _, end in timings):
                raise BridgeError('Corte ultrapassa a duração da mídia.')
            self._write(output/'source-probe.json', probe)
            project = output/'timeline.fcproj'
            imported = json.loads(self._run(['--save-as', project, 'import', source]))
            self._write(output/'import.json', imported)
            if imported.get('errors'):
                raise BridgeError('FilmCraft reportou erros ao importar a mídia.')
            tree = json.loads(self._run(['--project', project, 'inspect', 'project']))
            self._write(output/'project-imported.json', tree)
            # O ID de mídia é obtido da resposta real, nunca derivado do caminho.
            item = self._media_id(imported)
            steps = [{'id': 'file.newSequence', 'params': {'name': 'AutoCortes', 'width': spec['width'],
                       'height': spec['height'], 'fps': float(fps), 'video': 1, 'audio': 1}}]
            offset = 0
            srt = []
            expected = []
            expected_captions = []
            for i, (clip, (start, end)) in enumerate(zip(clips, timings), 1):
                length = end-start
                steps.append({'id': 'timeline.place', 'params': {'item': item, 'track': 'V1', 'audioTrack': 'A1',
                              'time': offset, 'sourceIn': start, 'duration': length, 'insert': False}})
                captions = clip.get('captions') if 'captions' in clip else [
                    {'start_ms': 0, 'end_ms': round(length*1000/TICKS),
                     'text': clip.get('caption') or clip.get('title') or clip['id']}]
                for cue in captions:
                    begin = min(length, snap_ms(cue['start_ms'], fps))
                    finish = min(length, snap_ms(cue['end_ms'], fps))
                    text = str(cue['text']).replace('\r', ' ').replace('\n', ' ')
                    if finish <= begin or not text.strip():
                        continue
                    expected_captions.append({'start': offset+begin, 'end': offset+finish})
                    srt.append(f'{len(srt)+1}\n{self._srt_time(offset+begin)} --> {self._srt_time(offset+finish)}\n{text}\n')
                expected.append({'id': clip['id'], 'start': offset, 'end': offset+length,
                                 'sourceIn': start, 'sourceOut': end})
                offset += length
            subtitle = output/'captions.srt'
            if not pause:  # com remoção de pausas as legendas nascem da transcrição já cortada
                subtitle.write_text('\n'.join(srt), encoding='utf-8')
                if srt:
                    steps.append({'id': 'captions.import', 'params': {'path': str(subtitle), 'name': 'AutoCortes'}})
            script = output/'commands.jsonl'
            script.write_text('\n'.join(json.dumps(s, ensure_ascii=False) for s in steps), encoding='utf-8')
            self._run(['--project', project, '--save', 'run', script])
            sequence = json.loads(self._run(['--project', project, 'inspect', 'sequence']))
            self._write(output/'sequence.json', sequence)
            self._write(output/'expected-timeline.json', expected)
            has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])
            self._validate_timeline(sequence, expected, fps, has_audio)
            removal = None
            if pause:
                removal = self.remove_pauses(project, item, words, pause, expected, fps, has_audio, output)
                offset = removal['duration_after_ticks']
            else:
                captions = json.loads(self._run(['--project', project, 'exec', 'captions.list']))
                self._write(output/'captions.json', captions)
                actual_captions = [c for t in captions['tracks'] for c in t['captions']]
                if len(actual_captions) != len(expected_captions) or any(abs(c[k]-e[k]) > TICKS/1000
                        for c, e in zip(actual_captions, expected_captions) for k in ('start', 'end')):
                    raise BridgeError('Legendas importadas divergem da montagem.')
            partial = output/'render.partial.mp4'
            self._run(['--project', project, 'export', partial, '--format', 'h264', '--gpu-rendering', 'off',
                       '--settings', json.dumps({'burnCaptions': True})])
            result = json.loads(self._run(['-v', 'error', '-show_streams', '-show_format', '-of', 'json', partial],
                                         program=shutil.which('ffprobe') or 'ffprobe'))
            actual = float(result['format']['duration'])
            if abs(actual-offset/TICKS) > max(0.15, 2/float(fps)):
                raise BridgeError('Exportação com duração divergente.')
            if not any(s['codec_type'] == 'video' and s['width'] == spec['width'] and s['height'] == spec['height']
                       for s in result['streams']):
                raise BridgeError('Exportação com resolução divergente.')
            rendered_video = next(s for s in result['streams'] if s['codec_type'] == 'video')
            if Fraction(rendered_video['avg_frame_rate']) != fps:
                raise BridgeError('Exportação com FPS divergente.')
            if any(s['codec_type'] == 'audio' for s in probe['streams']) and not any(
                    s['codec_type'] == 'audio' for s in result['streams']):
                raise BridgeError('Áudio ausente na exportação.')
            rendered_audio = next((s for s in result['streams'] if s['codec_type'] == 'audio'), None)
            if rendered_audio and (abs(float(rendered_audio.get('start_time', 0))-
                                      float(rendered_video.get('start_time', 0))) > 0.1 or
                                   abs(float(rendered_audio['duration'])-float(rendered_video['duration'])) > 0.1):
                raise BridgeError('Duração/início de áudio e vídeo divergentes.')
            self._run(['-v', 'error', '-xerror', '-i', partial, '-f', 'null', '-'],
                      program=shutil.which('ffmpeg') or 'ffmpeg')
            partial.replace(output/'render.mp4')
            self._write(output/'validation.json', {'valid': True, 'expected_duration': offset/TICKS, 'probe': result})
            report = {'project': str(project), 'render': str(output/'render.mp4'), 'valid': True}
            if removal:
                report['pause_removal'] = {k: removal[k] for k in ('removed', 'seconds', 'duration_before', 'duration_after')}
            return report
        except BaseException as exc:
            self._write(output/'failure.json', {'valid': False, 'error': str(exc), 'type': type(exc).__name__})
            raise
        finally:
            self._write(output/'bridge-log.json', self.log)

    def _run_script(self, project, steps, script):
        """Executa comandos em ordem e devolve ``{id: resultado}``; qualquer falha interrompe."""
        script.write_text('\n'.join(json.dumps(s, ensure_ascii=False) for s in steps), encoding='utf-8')
        stdout = self._run(['--project', project, '--save', '--compact', 'run', script])
        try:
            results = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        except json.JSONDecodeError:
            raise BridgeError('Resposta do FilmCraft não reconhecida ao executar os comandos.') from None
        if [r.get('id') for r in results] != [s['id'] for s in steps] or not all(r.get('ok') for r in results):
            raise BridgeError('O FilmCraft não concluiu todos os comandos: ' + json.dumps(results, ensure_ascii=False)[:600])
        return {r['id']: r.get('result') for r in results}

    def remove_pauses(self, project, item, words, pause, expected, fps, has_audio, output):
        """Fase opcional: ``transcript.set`` com as palavras da transcrição, ``transcript.removePauses`` e
        legendas recriadas (``transcript.createCaptions``) sobre a linha do tempo já cortada.

        Depende só do CLI do FilmCraft. Devolve o relatório validado; levanta ``BridgeError`` se a
        linha do tempo resultante não for coerente com os cortes originais.
        """
        before = json.loads(self._run(['--project', project, 'inspect', 'sequence']))
        subtitle = output/'captions.srt'
        results = self._run_script(project, [
            {'id': 'transcript.set', 'params': {'item': item, 'transcript': words}},
            {'id': 'transcript.removePauses', 'params': {'minSeconds': pause['min_seconds'],
                                                         'keepSeconds': pause['keep_seconds']}},
            {'id': 'transcript.createCaptions', 'params': {'maxChars': 38, 'lines': 1, 'name': 'AutoCortes'}},
            {'id': 'captions.export', 'params': {'path': str(subtitle), 'format': 'srt'}},
        ], output/'pause-commands.jsonl')
        if (results['transcript.set'] or {}).get('words') != len(words['words']):
            raise BridgeError('O FilmCraft não aceitou todas as palavras da transcrição.')
        after = json.loads(self._run(['--project', project, 'inspect', 'sequence']))
        self._write(output/'sequence-after-pauses.json', after)
        report = self._validate_pause_removal(before, after, expected, results['transcript.removePauses'], fps, has_audio)
        captions = json.loads(self._run(['--project', project, 'exec', 'captions.list']))
        self._write(output/'captions.json', captions)
        self._validate_pause_captions(captions, report['duration_after_ticks'], fps)
        if not subtitle.is_file() or subtitle.stat().st_size == 0:
            raise BridgeError('O FilmCraft não gravou o SRT da transcrição cortada.')
        report['words'] = len(words['words'])
        self._write(output/'pause-removal.json', {**report, 'options': pause})
        return report

    @staticmethod
    def _validate_pause_removal(before, after, expected, result, fps, has_audio):
        """Confere a linha do tempo depois de ``transcript.removePauses`` (as posições exatas mudam)."""
        frame = TICKS / fps
        tolerance = frame / 2

        def track(sequence, kind):
            return sorted((c for t in sequence[kind] for c in t['items']), key=lambda c: c['start'])
        original, remaining = track(before, 'video'), track(after, 'video')
        if not original or not remaining:
            raise BridgeError('A remoção de pausas esvaziou a montagem.')
        total_before, cursor = max(c['end'] for c in original), 0
        for clip in remaining:
            if clip['start'] != cursor or clip['end'] <= clip['start']:
                raise BridgeError('A remoção de pausas deixou lacuna ou sobreposição na linha do tempo.')
            cursor = clip['end']
        total_after = cursor
        position, previous_out = 0, None
        for clip in remaining:  # cada trecho restante vem de um corte original, na mesma ordem
            started_at = position
            while position < len(expected) and not (
                    expected[position]['sourceIn'] - tolerance <= clip['sourceIn'] and
                    clip['sourceOut'] <= expected[position]['sourceOut'] + tolerance):
                position += 1
            if position == len(expected):
                raise BridgeError('Um trecho restante não pertence a nenhum corte original.')
            if position != started_at:
                previous_out = None
            if previous_out is not None and clip['sourceIn'] < previous_out - tolerance:
                raise BridgeError('Os trechos restantes não seguem a ordem do corte original.')
            previous_out = clip['sourceOut']
        if has_audio:
            keys = ('start', 'end', 'sourceIn', 'sourceOut')
            audio = track(after, 'audio')
            if len(audio) != len(remaining) or any(a[k] != v[k] for a, v in zip(audio, remaining) for k in keys):
                raise BridgeError('Áudio e vídeo divergem depois de remover as pausas.')
        removed_ticks = (result or {}).get('ticks')
        if isinstance(removed_ticks, bool) or not isinstance(removed_ticks, (int, float)) or removed_ticks < 0:
            raise BridgeError('O FilmCraft não informou a duração removida.')
        if total_after > total_before or abs((total_before-total_after)-removed_ticks) > frame:
            raise BridgeError('A duração removida diverge do que o FilmCraft relatou.')
        return {'removed': int((result or {}).get('removed') or 0), 'seconds': round(removed_ticks/TICKS, 3),
                'duration_before': round(total_before/TICKS, 3), 'duration_after': round(total_after/TICKS, 3),
                'duration_after_ticks': total_after}

    @staticmethod
    def _validate_pause_captions(captions, total, fps):
        items = [c for t in captions.get('tracks', []) for c in t.get('captions', [])]
        if not items:
            raise BridgeError('Nenhuma legenda foi criada a partir da transcrição cortada.')
        frame = TICKS / fps
        if any(c['start'] < 0 or c['end'] <= c['start'] or c['end'] > total + frame for c in items):
            raise BridgeError('Legendas fora da duração da linha do tempo cortada.')

    @staticmethod
    def _media_id(imported):
        if isinstance(imported, dict) and imported.get('items'):
            value = imported['items'][0]
            return value['id'] if isinstance(value, dict) else value
        raise BridgeError('Resposta de importação não reconhecida; consulte import.json.')

    @staticmethod
    def _validate_timeline(sequence, expected, fps, has_audio):
        rate = sequence['settings']['frame_rate']
        if Fraction(rate['num'], rate['den']) != fps:
            raise BridgeError('FPS da sequência divergente.')
        for kind in ('video', 'audio') if has_audio else ('video',):
            actual = [clip for track in sequence[kind] for clip in track['items']]
            actual.sort(key=lambda c: c['start'])
            if len(actual) != len(expected) or any(c[key] != e[key]
                    for c, e in zip(actual, expected) for key in ('start', 'end', 'sourceIn', 'sourceOut')):
                raise BridgeError(f'Trilha {kind} diverge das decisões de corte.')

    @staticmethod
    def _write(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _srt_time(ticks):
        ms = round(Fraction(ticks*1000, TICKS))
        seconds, ms = divmod(ms, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'
