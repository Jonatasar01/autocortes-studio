import argparse
import json
from pathlib import Path
from .bridge import FilmCraftBridge, BridgeError

parser = argparse.ArgumentParser(description='Protótipo FilmCraft isolado do exportador FFmpeg')
parser.add_argument('--binary')
parser.add_argument('--diagnose', action='store_true')
source = parser.add_mutually_exclusive_group()
source.add_argument('--manifest', type=Path)
source.add_argument('--project-id')
parser.add_argument('--clips', type=int, nargs='+')
parser.add_argument('--out', type=Path)
parser.add_argument('--timeout', type=float, default=180)
args = parser.parse_args()
try:
    bridge = FilmCraftBridge(args.binary, timeout=args.timeout)
    if args.diagnose:
        result = bridge.diagnose()
    else:
        if not (args.manifest or args.project_id) or not args.out:
            parser.error('Informe --manifest ou --project-id, junto com --out, ou use --diagnose.')
        manifest = (bridge.decision_from_project(args.project_id, args.clips) if args.project_id else
                    json.loads(args.manifest.read_text(encoding='utf-8')))
        result = bridge.build(manifest, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
except (BridgeError, OSError, ValueError, KeyError, TypeError) as exc:
    parser.exit(1, f'FilmCraft indisponível/falhou: {exc}\nO exportador FFmpeg do AutoCortes permanece independente.\n')
