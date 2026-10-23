# Prompt para o Codex — AutoCortes Studio Local (continuação a partir da v2.2.0)

> Cole este texto no Codex com a pasta `AutoCortes-Studio-Local/` aberta como repositório.
> Histórico: `EVOLUCAO_CODEX_CLAUDE.md` (MVP), `ENTREGA_ETAPA1.md` (v2.1.1), `AUDITORIA.md` §4.3 (v2.2).

## 1. Missão

Atue como uma equipe sênior de engenharia com:

- Python, FastAPI e JavaScript
- FFmpeg e vídeo
- IA aplicada (Whisper, Ollama e OpenAI)
- segurança, QA e UI/UX
- políticas de direitos autorais de plataformas

**Evolua o projeto existente; não reescreva do zero.** Preserve todas as funcionalidades e testes.

O objetivo é um app local que transforma vídeos longos em cortes prontos para revisão com o mínimo de intervenção. Isso já existe na v2.2 pelo **piloto automático**, pela **pasta de entrada monitorada** e pela **pasta de saída com pacote de publicação**.

**Prioridade máxima:** confiabilidade da geração de vídeos e ausência de regressões.

## 2. Antes de mudar qualquer coisa

1. Leia `README.md`, `AUDITORIA.md` (especialmente §4 Estado de verificação), `CHANGELOG.md`, `ENTREGA_ETAPA1.md` e todo o código em `app/` e `tests/`.
2. Prepare o ambiente:
   `pip install -r requirements-dev.txt -r requirements-ai.txt -r requirements-music.txt`
3. Confira o FFmpeg com `ffmpeg_capabilities()`: ffmpeg, ffprobe, libass e libx264 devem estar presentes.
4. Rode `pytest -q -ra` e registre o resultado. A v2.2 foi verificada sem pytest e sem FastAPI:
   - 98 testes via `unittest` (91 aprovados e 7 da API pulados) e 10 de `test_regressions_v21.py` via substituto;
   - **`tests/test_api.py` (7 testes, incluindo `test_automation_endpoints`) ainda não foi executado na v2.2.**

   **Primeira tarefa:** rodar tudo no Windows e no Linux e corrigir o que falhar.
5. Nada é dado como pronto sem teste executado. O que não puder ser testado deve ser registrado como **não verificado**.

## 3. Arquitetura atual (respeite as camadas)

```
app/main.py          API FastAPI fina (rotas → service)
app/security.py      Host permitido, Origin local, X-AutoCortes: 1 em requisições que alteram dados, limite de corpo, CSP
app/service.py       Regras de negócio; registra handlers da fila e ganchos do piloto
app/jobs.py          Fila SQLite: estados, progresso, eventos, dedupe, recuperação, novas tentativas (attempts/not_before)
app/pipeline.py      run_analysis, run_export (pausas, narração, pacote, pasta de saída), run_narration
app/autopilot.py     Seleção automática, encadeamento roteiro→exportação, pausa por direitos, retomada, run_now
app/settings.py      data/settings.json validado (sem segredos, sem caminhos)
app/watcher.py       Pasta de entrada: estabilidade, duplicatas por impressão digital, processados/falhas
app/publish.py       Pacote de publicação e cópia atômica para a pasta de saída
app/media.py         ffprobe, FFmpeg com progresso/cancelamento, render 9:16/16:9/1:1/original, mixagem com ducking,
                     encurtar pausas (select/aselect alinhados aos quadros), validação do MP4
app/subtitles.py, transcription.py, selection.py, narration.py, rights.py, music.py, storage.py, config.py, errors.py
app/engine.py        Compatibilidade (não remover)
app/static/          HTML/CSS/JS sem framework; CSP 'self' (sem inline, sem eval)
```

## 4. Regras invioláveis

- **Local primeiro:** somente `127.0.0.1`. Qualquer acesso remoto exige autenticação e TLS antes.
- **Nada sai do computador sem opt-in registrado** (ACRCloud, OpenAI).
  - O piloto só narra com o consentimento permanente das Configurações, e cada envio é registrado em `openai_consents`.
  - Chaves só em variáveis de ambiente.
- **Subprocessos:** lista de argumentos (sem shell). Nenhum caminho do usuário dentro de filtros do FFmpeg.
- **Publicação de arquivos:**
  - nenhum arquivo é publicado sem `media.validate_output`;
  - publicação atômica e versionada;
  - escritas em `project.json` só via `storage.edit_project`;
  - cópias para a pasta de saída via `.part` + `os.replace`.
- **Automação nunca decide por você sobre direitos:**
  - risco alto pausa o piloto;
  - nada é confirmado em nome do usuário;
  - não existe estado "livre";
  - não simule o Content ID;
  - **nenhuma técnica de evasão** (pitch, velocidade, espelhamento ou filtros para enganar detectores);
  - narração não licencia material de terceiros.
- **Honestidade sobre a IA:**
  - cortes temporais não recebem nota e só entram no piloto se o usuário permitir;
  - respostas de IA são validadas;
  - transcrição e contexto são dados não confiáveis;
  - títulos sem respaldo no texto são descartados;
  - o pacote de publicação só usa o próprio conteúdo.
- **Aviso de voz por IA:** obrigatório sempre que houver narração (política da OpenAI).
- **Pastas de entrada e saída:** só mudam por variável de ambiente; a interface não grava caminhos arbitrários.
- **Testes:** nunca escrevem em `entrada/`, `saida/` ou `data/` reais (veja `tests/__init__.py`).
- **Compatibilidade:** Windows 10/11 é prioritário; use Python 3.10+.

## 5. Trabalho, em ordem

### Etapa A — Fechar a verificação da v2.2

1. Execute `tests/test_api.py` e toda a suíte com pytest no Windows e no Linux (use o workflow em `.github/workflows/tests.yml`).
2. **Pasta de entrada no Windows:**
   - arquivo grande sendo copiado pelo Explorer (não pode ser importado antes de terminar);
   - arquivo aberto em outro programa;
   - nomes com acentos;
   - encerramento do app no meio (o arquivo em `.processando` deve voltar para `entrada/`).
3. **Teste de resistência:** 10 vídeos na pasta de entrada com o piloto ativo e 2 formatos. Meça tempo, uso de disco, descritores e handles abertos e memória. Confirme que não sobram temporários.
4. **OpenAI real (se houver chave):** roteiro, voz, nova tentativa em 429 e fallback sem narração. Confira a data de retirada dos snapshots do TTS (06/01/2027) e prepare a migração.
5. Atualize a §4 de `AUDITORIA.md` com evidências.

### Etapa B — Qualidade automática dos cortes

1. **Reenquadramento por rosto/falante** (MediaPipe ou similar, opcional em `requirements-ai.txt`), com suavização de trajetória e ajuste manual do centro. Mantenha `fit` e `blur` como alternativa segura.
   - **Piloto:** novo modo de enquadramento `smart`, que recua para `blur` quando a confiança for baixa.
2. **Legendas:**
   - edição do texto dos blocos antes de exportar;
   - estilos (tamanho, cor, contorno, posição);
   - destaque palavra a palavra usando os word timestamps;
   - preset de estilo no perfil do piloto.
3. **Pré-visualização do MP4 exportado** no app e **pré-escuta** de cada bloco de narração.
4. **Orçamento de custo da OpenAI** por projeto e por dia nas Configurações: estimativa de caracteres e tokens antes do envio, e o piloto para ao atingir o limite.
5. **Encurtar pausas:** pré-visualização da linha do tempo resultante e compatibilidade com narração (remapear os blocos).

### Etapa C — Produtividade

1. **Linha do tempo visual** (miniaturas e forma de onda), ajuste por arraste, atalhos e desfazer/refazer. Se usar framework, mantenha a CSP e o build local.
2. **Agendamento e limites do piloto:** processar só em horários definidos; pausar quando houver pouco espaço em disco ou em uso de bateria (notebooks).
3. **Notificações** do navegador ao concluir ou quando o piloto pausar por direitos, com permissão do usuário.
4. **Limpeza automática opcional:** remover originais de projetos concluídos depois de N dias, com confirmação e sem tocar em `saida/`.

### Etapa D — Direitos e publicação

1. **Catálogo local de ativos próprios ou licenciados** (impressão digital de áudio). Uma correspondência vale como "documentado"; a ausência continua "não verificado".
2. **Substituição de trilha** por ativo autorizado e reexportação. Nunca para camuflar música detectada.
3. **Relatório PDF opcional**, com trilha de auditoria (declarações, consentimentos, confirmações, decisões do piloto).
4. **Upload ao YouTube** somente com OAuth explícito:
   - sempre como privado ou não listado;
   - divulgação de conteúdo sintético quando houver narração;
   - instrução para conferir "Verificações" no YouTube Studio;
   - **nunca publicar automaticamente como público** e nunca afirmar verificação de direitos.

### Etapa E — Empacotamento

1. **Instalador Windows** (justifique: PyInstaller, Briefcase, Tauri ou Electron):
   - FFmpeg incluído ou localizado;
   - atalho;
   - opção de iniciar com o Windows, para a pasta monitorada funcionar sem abrir o app manualmente;
   - desinstalação que preserva `data/`, `entrada/` e `saida/` se o usuário quiser.
2. **CI** executando de fato no GitHub, com artefatos.

## 6. Critérios de aceite

- `pytest` 100% aprovado no Windows e no Linux, e cada recurso novo com testes que usam vídeos sintéticos gerados por FFmpeg.
- **Cenários obrigatórios** continuam cobertos:
  - contêineres MP4, MOV, MKV, AVI, WEBM e M4V
  - vídeo vertical, quadrado, 4K, rotacionado, VFR, sem áudio, curto e longo
  - arquivo corrompido ou truncado
  - nomes especiais
  - Ollama e OpenAI indisponíveis ou com JSON inválido
  - FFmpeg sem libass
  - cancelamento e recuperação
  - edição concorrente
  - pasta de entrada com arquivo parcial, duplicado ou corrompido
  - piloto pausado por risco alto
  - novas tentativas automáticas
- Nenhum arquivo publicado sem validação. Nenhuma mensagem ao usuário com caminho local, chave ou stack trace.
- Interface acessível por teclado, sem rolagem horizontal em 390 px, sem erros de console e compatível com a CSP.
- `README.md`, `CHANGELOG.md` e `AUDITORIA.md` atualizados.

## 7. Entrega de cada etapa

1. O que foi implementado, por arquivo.
2. Saída do pytest e evidências: prints, quadros extraídos dos vídeos, métricas do teste de resistência.
3. Decisões e alternativas descartadas.
4. O que ficou **não verificado** e por quê.
5. Riscos remanescentes.

Entregue código funcionando e testado, nunca só um plano.
