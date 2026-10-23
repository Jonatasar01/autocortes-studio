"""Teste real opcional: python scripts/filmcraft_smoke.py --binary PATH --out NOVA_PASTA."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.integrations.filmcraft.bridge import FilmCraftBridge, sha256

parser = argparse.ArgumentParser()
parser.add_argument('--binary', required=True)
parser.add_argument('--out', required=True, type=Path)
parser.add_argument('--no-audio', action='store_true')
args = parser.parse_args()
root = args.out.resolve()
root.mkdir(parents=True, exist_ok=False)
source = root/'source.mp4'
command = [shutil.which('ffmpeg') or 'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
           'testsrc2=size=320x180:rate=30000/1001:duration=8']
if not args.no_audio:
    command += ['-f', 'lavfi', '-i', 'sine=frequency=600:sample_rate=48000:duration=8']
command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'ultrafast']
if not args.no_audio:
    command += ['-c:a', 'aac', '-shortest']
subprocess.run([*command, str(source)], check=True, timeout=60)
manifest = {'schema_version': 'autocortes-edit-v1', 'caption_mode': 'transcript',
            'source': {'path': str(source), 'sha256': sha256(source), 'fps': {'num': 30000, 'den': 1001}},
            'clips': [{'id': 'clip-001', 'source_start_ms': 1001, 'source_end_ms': 3003,
                       'title': 'Primeiro corte — ação e áudio', 'rights_state': 'synthetic',
                       'captions': [{'start_ms': 0, 'end_ms': 800, 'text': 'Primeira frase.'}, {'start_ms': 900, 'end_ms': 1800, 'text': 'Segunda frase.'}]},
                      {'id': 'clip-002', 'source_start_ms': 5005, 'source_end_ms': 7007,
                       'title': 'Segundo corte: precisão temporal', 'rights_state': 'synthetic',
                       'captions': [{'start_ms': 100, 'end_ms': 1900, 'text': 'Outra fala do teste.'}]}],
            'requested_output': {'width': 320, 'height': 180, 'fps': {'num': 30000, 'den': 1001}}}
(root/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(FilmCraftBridge(args.binary).build(manifest, root/'result'), indent=2))
