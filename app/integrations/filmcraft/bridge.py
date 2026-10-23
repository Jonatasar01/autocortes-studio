from __future__ import annotations

import hashlib
import json
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


class BridgeError(RuntimeError):
    pass


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

    def diagnose(self):
        version = self._run(['--version']).strip()
        if version != f'filmcraft-cli {SUPPORTED_VERSION}':
            raise BridgeError(f'Versão ainda não validada: {version}. Esperado {SUPPORTED_VERSION}.')
        commands = json.loads(self._run(['commands', '--json']))
        missing = REQUIRED - {item['id'] for item in commands}
        if missing:
            raise BridgeError('Comandos ausentes: ' + ', '.join(sorted(missing)))
        return {'version': version, 'binary': str(self.binary), 'sha256': sha256(self.binary),
                'commands': commands, 'platform': os.name}

    def decision_from_project(self, project_id, clip_ids=None):
        from ... import storage, subtitles
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
        return {'schema_version': 'autocortes-edit-v1', 'origin_project_id': project_id,
                'rights': project.get('rights'), 'caption_mode': 'transcript',
                'source': {'path': str(source), 'sha256': sha256(source), 'fps': rate},
                'clips': [{'id': f"clip-{c['id']:03d}", 'revision': c.get('revision', 1),
                           'source_start_ms': ms(c['start']), 'source_end_ms': ms(c['end']),
                           'title': c['title'], 'score': c.get('score'), 'framing': 'fit',
                           'captions': [{'start_ms': ms(cue['start']), 'end_ms': ms(cue['end']), 'text': cue['text']}
                                        for cue in subtitles.build_cues(project.get('transcript') or [], c['start'], c['end'], 38)]}
                          for c in clips],
                'requested_output': {'width': video['width'], 'height': video['height'], 'fps': rate}}

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
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=False)
        try:
            diagnosis = self.diagnose()
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
            return {'project': str(project), 'render': str(output/'render.mp4'), 'valid': True}
        except BaseException as exc:
            self._write(output/'failure.json', {'valid': False, 'error': str(exc), 'type': type(exc).__name__})
            raise
        finally:
            self._write(output/'bridge-log.json', self.log)

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
