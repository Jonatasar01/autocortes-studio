"""AutoCortes em lote: renderiza os cortes marcados em um CSV.

Uso:
    python main.py cortes.csv
    python main.py cortes.csv --saida output --palavras 4 --fonte "Montserrat ExtraBold"

Colunas do CSV: video_origem, tempo_inicio, tempo_fim, layout [, nome]
Layouts: single_speaker, split_screen, pip. Saída: MP4 1080×1920 (9:16) com legenda embutida.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.batch.ingest import CsvError
from app.batch.runner import Options, run_batch
from app.errors import Cancelled


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Renderiza em lote os cortes marcados em um CSV.')
    p.add_argument('csv', type=Path, help='arquivo de marcação (ex.: cortes.csv)')
    p.add_argument('--saida', type=Path, default=Path('output'), help='pasta dos vídeos finais (padrão: output)')
    p.add_argument('--palavras', type=int, default=4, choices=(3, 4, 5), help='palavras por bloco de legenda')
    p.add_argument('--fonte', default='Arial Black', help='fonte da legenda (ex.: "Montserrat ExtraBold")')
    p.add_argument('--sem-legenda', action='store_true', help='não transcreve nem embute legendas')
    p.add_argument('--minusculas', action='store_true', help='mantém a caixa original do texto da legenda')
    p.add_argument('--manter-faixas', action='store_true',
                   help='não remove as faixas pretas embutidas no vídeo original')
    p.add_argument('--rosto', choices=('auto', 'sim', 'nao'), default='auto',
                   help='rastrear rosto no single_speaker (auto = se o OpenCV estiver instalado)')
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        # Console do Windows (cp1252/cp850): acentos e símbolos não derrubam o lote.
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    opts = Options(output_dir=args.saida.resolve(), captions=not args.sem_legenda, max_words=args.palavras,
                   font=args.fonte, uppercase=not args.minusculas, face_tracking=args.rosto,
                   remove_bars=not args.manter_faixas)
    try:
        results, skipped = run_batch(args.csv, opts)
    except CsvError as exc:
        print(f'ERRO: {exc}', file=sys.stderr)
        return 2
    except (Cancelled, KeyboardInterrupt):
        print('\nInterrompido. Arquivos temporários da linha atual foram removidos.', file=sys.stderr)
        return 130
    ok = sum(r.ok for r in results)
    print(f'\nConcluído: {ok} de {len(results)} corte(s) gerado(s) em {opts.output_dir}'
          + (f'; {len(skipped)} linha(s) inválida(s) ignorada(s).' if skipped else '.'))
    return 0 if ok == len(results) and not skipped else 1


if __name__ == '__main__':
    sys.exit(main())
