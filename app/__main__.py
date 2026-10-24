"""Inicia o servidor: ``python -m app``.

Por padrão escuta só em 127.0.0.1. Para abrir para outros aparelhos da rede defina
``AUTOCORTES_HOST=0.0.0.0`` e ``AUTOCORTES_PASSWORD``; sem senha o servidor se recusa a expor.
"""
from __future__ import annotations

import sys

from . import config


def main() -> int:
    if config.is_exposed() and not config.PASSWORD:
        print(f'ERRO: AUTOCORTES_HOST={config.HOST} deixa o app visível para outros aparelhos, mas não há senha.\n'
              'Defina AUTOCORTES_PASSWORD (ex.: set AUTOCORTES_PASSWORD=minha-senha) e inicie de novo.',
              file=sys.stderr)
        return 2
    print(f'AutoCortes neste computador: http://127.0.0.1:{config.PORT}')
    if config.is_exposed():
        for ip in config.local_ips():
            print(f'Em outros aparelhos da rede: http://{ip}:{config.PORT}  (qualquer usuário + a senha definida)')
    import uvicorn
    uvicorn.run('app.main:app', host=config.HOST, port=config.PORT, workers=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
