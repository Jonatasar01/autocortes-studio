# Instalação reproduzível e arquitetura

Esta edição v2.3.1 acrescenta infraestrutura e corrige as três falhas de automação registradas na revisão anterior: hash parcial de vídeos, mistura de execuções do piloto e falhas de roteiro exibidas como sucesso. O hash completo é calculado em blocos, com memória limitada; hashes antigos são recalculados a partir dos vídeos originais quando necessário. Cada execução recebe um identificador e aguarda o encadeamento de narração/exportação antes de concluir. A inicialização recupera callbacks de narração pendentes. Os algoritmos de visão, transcrição e a interface permanecem os mesmos.

## uv (execução nativa)

Instale uv e FFmpeg com libass/libx264. Na pasta do projeto:

```sh
uv sync --locked --extra ai --extra music --no-dev --python 3.12
uv run --no-sync python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

`pyproject.toml` define as dependências e `uv.lock` fixa a resolução. O extra `ai` inclui faster-whisper e OpenCV; `music` inclui o cliente HTTP opcional. Sem `--extra ai`, a instalação básica não inclui transcrição ou rastreamento facial. Os lançadores BAT/SH existentes continuam disponíveis com os arquivos requirements exportados do lockfile.

Para desenvolvimento:

```sh
uv sync --locked --all-extras
uv run --no-sync python -m pytest -q
```

Para atualizar dependências deliberadamente, edite pyproject.toml e execute `uv lock`, ou `uv lock --upgrade` para atualizar todas as versões permitidas. Depois execute os testes e regenere os arquivos de compatibilidade:

```sh
uv export --locked --no-dev --no-hashes --format requirements-txt --output-file requirements.txt
uv export --locked --no-dev --extra ai --no-hashes --format requirements-txt --output-file requirements-ai.txt
uv export --locked --no-dev --extra music --no-hashes --format requirements-txt --output-file requirements-music.txt
uv export --locked --no-hashes --format requirements-txt --output-file requirements-dev.txt
```

## Docker Compose (CPU)

Requer Docker Engine e Compose, ou Docker Desktop com contêineres Linux no Windows. Na pasta do projeto:

```sh
docker compose up --build -d
docker compose ps
docker compose logs --tail 100 autocortes
```

Abra http://127.0.0.1:8002. A porta 8002 evita conflito com o servidor nativo de revisão em 8001. Pode ser alterada por `AUTOCORTES_PORT`. O processo escuta em 0.0.0.0 dentro do contêiner para permitir o encaminhamento; a publicação no computador fica em 127.0.0.1. O contêiner executa como usuário sem privilégios, com um único worker da API.

Os volumes `state` (projetos, SQLite, configurações, entrada e saída) e `models` (pesos de transcrição) sobrevivem à recriação do contêiner. O primeiro uso do Whisper exige rede para baixar o modelo. Os dados da instalação nativa não são migrados automaticamente. Faça upload pela interface ou, após habilitar o monitor de pasta nas Configurações, copie um arquivo:

```sh
docker compose cp video.mp4 autocortes:/state/entrada/video.mp4.part
docker compose exec autocortes mv /state/entrada/video.mp4.part /state/entrada/video.mp4
docker compose cp autocortes:/state/saida ./saida-exportada
```

O sufixo `.part` impede o monitor de importar uma cópia incompleta. Para backup consistente, pare o processamento e o serviço antes de copiar `/state` usando `docker compose cp`. `docker compose down` preserva volumes; **não use `down -v` se quiser preservar projetos e modelos**.

Chaves opcionais podem ser fornecidas por variáveis OPENAI_API_KEY e ANTHROPIC_API_KEY do host ou por um arquivo `.env` local. Elas não entram no contexto de build. O envio para serviços externos continua dependendo do consentimento da aplicação. GPU e Ollama do computador host não estão configurados nesta composição; o endereço 127.0.0.1 dentro do contêiner aponta para o próprio contêiner.

## Decisões de arquitetura

- **Transcrição:** o código já usa faster-whisper/CTranslate2, INT8 na CPU, cache do modelo e fallback de dispositivo. Não há migração do Whisper original a fazer.
- **Fila:** jobs.py já usa SQLite/WAL, limite de concorrência, cancelamento e tentativas para erros transitórios. No reinício, trabalhos em execução tornam-se `interrupted`; eles não continuam do ponto exato onde pararam. Celery/RQ não elimina falhas de renderização ou falta de memória. Uma migração distribuída exige também armazenamento compartilhado, idempotência e substituição dos locks de processo. Não executar múltiplas réplicas sobre este volume SQLite.
- **Rostos:** Haar permanece nesta edição. Uma troca por MediaPipe/YOLO exige benchmark com vídeos frontais, perfis, oclusões e múltiplos interlocutores, comparando precisão, tempo, RAM e estabilidade do recorte. Detecção melhor, isoladamente, não garante suavidade do rastreamento.
- **Interface:** permanece Vanilla JS. A migração deve separar cliente HTTP, estado e componentes, preservando cancelamento, progresso e consentimentos. Escolher React/Vue/Svelte, por si só, não comprova robustez ou desempenho.

## Fontes técnicas

- uv e Docker: https://docs.astral.sh/uv/guides/integration/docker/
- Dependências e extras: https://docs.astral.sh/uv/concepts/projects/dependencies/
- Lockfile e sincronização: https://docs.astral.sh/uv/concepts/projects/sync/
- Publicação de portas e serviços Compose: https://docs.docker.com/reference/compose-file/services/

As dependências Python estão travadas pelo lockfile; imagens base e pacotes apt não estão fixados por digest/snapshot, portanto isto não promete builds idênticos byte a byte.
