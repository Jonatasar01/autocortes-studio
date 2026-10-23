# Validação da v2.3.1 — 22/10/2026

Prioridade escolhida: estabilidade e instalação. Base: ZIP v2.3.0 fornecido pelo usuário. A versão original e seu servidor foram preservados.

## Entregue

- Hash SHA-256 completo em blocos para identificação de vídeos; migração sob demanda dos hashes parciais antigos.
- Identificador de execução do piloto em cada tarefa, isolando resultados de execuções anteriores.
- Contabilização de falhas de roteiro sem fallback bem-sucedido; verificação de exportações planejadas ausentes.
- Coordenação entre submissão, conclusão e callbacks de narração; recuperação de callbacks pendentes após reinício e proteção contra callbacks atrasados.
- Cancelamento de tarefas sem executar callbacks sob o lock SQLite.
- pyproject.toml, uv.lock e requirements exportados. Python 3.12 como versão padrão.
- Dockerfile para CPU e docker-compose.yml com volumes persistentes, usuário sem privilégios, healthcheck e porta publicada apenas em localhost.
- CI atualizado para uv e workflow adicional para testar build/inicialização do contêiner.

## Evidência executada

- Ambiente virtual novo instalado a partir do lockfile, com extras ai/music e grupo dev.
- Suíte final em Windows e Python 3.12.14: **150 testes e 35 subtestes passaram em 150,02 segundos**.
- FFmpeg real nos testes de mídia. Integrações de IA externa simuladas nos testes.
- 15 novos casos parametrizados/de regressão em tests/test_stability.py, além da extensão do teste integrado de duas execuções sucessivas do piloto.
- TOML e YAML analisados sem erro; verificados loopback da porta e volumes no Compose. Isso não equivale a executar o Docker.
- Servidor nativo: http://127.0.0.1:8003, /api/health HTTP 200 e versão 2.3.1.
- Chrome: página inicial sem erros de JavaScript observados; em viewport de 390 px, largura do documento também de 390 px.

O log completo da suíte final está em evidencias/v231-pytest.txt. Referências de alguns testes à pasta review-v230 no log decorrem de metadados de bytecode copiado; o processo executou a cópia modernizada, com os módulos alterados e o ambiente novo. O pacote de entrega exclui bytecode e ambientes virtuais.

## Limites

- Docker indisponível neste ambiente: imagem não construída e contêiner não executado. O workflow incluído também não foi executado remotamente nesta entrega. Para validar: `docker compose up --build --wait` e consultar /api/health em localhost:8002.
- Não houve chamadas reais à OpenAI/Anthropic, nem nova medição de precisão de rostos ou transcrição em vídeos reais nesta etapa.
- O servidor em 8003 tem armazenamento separado do servidor 2.3.0 em 8001. Projetos existentes não foram migrados automaticamente. Os dados podem ser preservados usando a mesma pasta AUTOCORTES_DATA em uma migração planejada, após parar a versão anterior e fazer backup; não execute duas versões sobre a mesma pasta.
- O primeiro recálculo de um hash legado exige ler o vídeo original inteiro. Arquivos posteriores com hash completo evitam esse recálculo.
- Tarefas de mídia interrompidas continuam exigindo nova execução; a recuperação de callbacks não é retomada de renderização a partir de checkpoint.
- MediaPipe/YOLO, framework de frontend e Celery/Redis não foram introduzidos nesta etapa. O código já usa faster-whisper e fila SQLite persistente. Veja INFRAESTRUTURA.md para as decisões e instruções de uso.

Documentos de auditoria e evidências anteriores incluídos no projeto descrevem versões anteriores; este arquivo registra os resultados específicos desta entrega.
