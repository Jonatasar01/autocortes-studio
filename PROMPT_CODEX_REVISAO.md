# Prompt para o Codex: revisão do AutoCortes Studio e relatório das análises

> Cole este texto no Codex com o repositório `autocortes-studio` aberto, no branch `feature/narrated-batch` (PR #10).

## 1. Missão

Você é um revisor sênior: Python, FastAPI, JavaScript, FFmpeg, faster-whisper/CTranslate2, QA e UI/UX.

**Esta tarefa é de verificação, não de desenvolvimento.**
- Não altere código, não faça commit, não faça push.
- Se achar um defeito, descreva-o com arquivo:linha, o cenário que falha e uma sugestão de correção. Não aplique a correção.

Entregue um relatório com:
1. o que você verificou e como;
2. o que passou, o que falhou e o que não pôde ser verificado;
3. as análises de cortes já realizadas nos projetos existentes (seção 5).

## 2. Contexto

App local (Windows, GPU GTX 1650 4 GB) que transforma vídeos longos, inclusive lives de 8,5 h, em cortes 9:16 com legenda.

Commits do PR #10, do mais antigo para o mais recente (`git log --oneline master..HEAD`):

| Commit | Mudança |
| --- | --- |
| a5cdc82 | Lote narrado: coluna de roteiro, edge-tts, mixagem com a voz em primeiro plano |
| 39764a4 | Seletor de estilo de legenda (a legenda animada não era enviada) |
| a938d1e | Seguir rosto: YuNet, filtro de oscilação, boca × testa para escolher quem fala |
| a58aae5, 5f9d505 | Limites de 50 GB de upload e 12 h de duração no `iniciar_windows.bat` |
| b21c164 | Transcrição em partes, transcrição e cenas na GPU, cenas em paralelo, percentual na tela |
| 9ac0822, d879c74 | Modo automático sem limite de quantidade: entram todos os cortes com nota ≥ 80 |
| 6665add | Legenda posicionada fora da borda do vídeo nos enquadramentos desfoque e encaixe |
| e62c6a2 | Seguir rosto em live de jogo: ignora rostos estáticos e pondera pelo movimento |
| 64edacf | Enquadramento "Por trecho" (desfoque, quadro inteiro e seguir rosto no mesmo corte) |
| b32126e | Enquadramento "Só o streamer" (janela da webcam com fundo desfocado) |
| 5f31b40 | Projetos rápidos: JSON compacto, transcrição em `transcript.json` separado |
| c3ad263 | Legenda precisa: retranscreve cada corte com large-v3-turbo ao exportar |
| e493ad4 | Linha do tempo nova; análise com large-v3-turbo na GPU; etapa, %, tempo restante e duração das etapas visíveis |

Arquivos centrais:
- análise: `app/pipeline.py`, `app/transcription.py`, `app/selection.py`, `app/curation.py`;
- enquadramento: `app/reframe.py`;
- legendas: `app/subtitles.py`, `app/textfit.py`;
- serviço e fila: `app/service.py`, `app/jobs.py`;
- dados: `app/storage.py`, `app/timeline.py`;
- API: `app/main.py`;
- interface: `app/static/studio.{html,js,css}`.

## 3. Regras

- **Não reinicie nem pare o servidor em 127.0.0.1:8000.** Reiniciar interrompe as análises em andamento.
  - Para testar a API, use os testes (TestClient) ou suba outra instância em outra porta, com outro `data/`.
- Não modifique nada em `data/` nem em `saida/`. Leia somente.
- Não sugira integrar nem copiar código do "Pipeline Canal Dark": a licença dele proíbe redistribuição.
- Não proponha recursos para driblar Content ID ou direitos autorais.
- O que não puder ser executado deve aparecer como **não verificado**, com o motivo.

## 4. Verificações

1. **Ambiente**
   - Use `.venv\Scripts\python.exe`.
   - Registre as versões de Python, FFmpeg (com libass e libx264), faster-whisper e ctranslate2.
   - Informe se a GPU com CUDA está disponível (`app.transcription` e `_cuda_dll_dirs`).
2. **Testes**
   - Rode `.venv\Scripts\python.exe -m unittest discover -s tests -t .`. A referência atual é 263 testes, OK, 2 pulados.
   - Se o pytest estiver instalado, rode também `pytest -q -ra`.
   - Anexe o resumo e qualquer falha completa.
3. **Revisão de código do PR**
   - Rode `git diff master...HEAD`.
   - Procure:
     - bugs de lógica e condições de corrida (análise em threads e cenas em paralelo);
     - vazamento de memória de GPU ou CPU em vídeos longos;
     - tratamento de cancelamento e de falha da GPU (queda para CPU);
     - caminhos de arquivo no Windows;
     - segurança das rotas novas (`/filmstrip`, `/waveform`).
   - Classifique cada achado como crítico, alto, médio ou baixo.
4. **Pontos específicos**
   - `WHISPER_MODEL=auto`: deve usar turbo com `int8_float16` na GPU e small com `int8` na CPU.
   - `_refine_transcript` não pode retranscrever quando o modelo da análise já é o de refinamento.
   - `list_projects()`: confira o campo `analysis` (etapa e progresso), o custo de consultar a fila por projeto e a atualização da lista a cada 5 s na interface.
   - Em `studio.js`, `analysisLabel`: confira se o tempo restante faz sentido no começo da etapa (fração ≈ 0), sem divisão por zero nem valores absurdos.
   - Na linha do tempo (`timeline.py` e `studio.js`):
     - cache do filmstrip e da forma de onda;
     - o arquivo de origem mudar depois que o cache foi criado;
     - desempenho do canvas com 8 h de vídeo e zoom máximo.
   - Seleção automática: limiar de nota ≥ 80, sobreposição e duplicatas entre cortes.
5. **Interface (se possível)**
   - Abra http://127.0.0.1:8000 sem alterar nada.
   - Confira a lista de projetos, um projeto em análise (etapas, %, tempo restante) e a linha do tempo de um projeto pronto.
   - Descreva o que viu. Anexe capturas de tela se a ferramenta permitir.

## 5. Relatório das análises de cortes já realizadas

Cada projeto é uma pasta `data/<id>/` com `project.json`, `transcript.json`, `clips/`, `cache/` e `source.mp4`. Para cada projeto, somente leitura, mostre:
- nome, duração do vídeo, status e data de criação;
- o modelo de transcrição usado (`project.json` → `transcription`);
- a duração de cada etapa da análise e o tempo total.
  - Os eventos dos jobs ficam em `data/autocortes.db` (SQLite, via `app/jobs.py`).
  - Abra o banco em modo somente leitura (`file:...?mode=ro`), porque o servidor está usando-o.
  - Outra opção é `GET /api/projects/<id>`, que traz `analysis_events`.
  - Compare as análises do mesmo vídeo de 8,5 h: a anterior com o modelo `small` e a nova com `large-v3-turbo`, se já tiver terminado.
- os cortes em uma tabela:

  | # | início–fim | duração | nota | título/motivo | enquadramento |
  | --- | --- | --- | --- | --- | --- |

- quais cortes já foram exportados (`saida/`) e se a legenda foi refinada (`transcript_refined`).

Se existir `project.antes-ranking.json`, compare com o `project.json` atual e diga o que a seleção automática mudou.

## 6. Formato da entrega

1. **Resumo executivo**: no máximo 10 linhas, com o veredito do PR: pronto, pronto com ressalvas ou não pronto.
2. Ambiente e resultados dos testes.
3. Achados da revisão, em tabela ordenada por severidade: severidade, arquivo:linha, problema, cenário, sugestão.
4. Relatório das análises (seção 5).
5. **Não verificado**: lista do que ficou sem verificação e por quê.
6. Sugestões de melhoria priorizadas, cada uma com esforço estimado: pequeno, médio ou grande.
