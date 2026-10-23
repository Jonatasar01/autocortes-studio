# Auditoria técnica — AutoCortes Studio Local

Data: 21/10/2026 · Escopo: todo o repositório recebido (`app/`, `tests/`, scripts, requisitos e documentação).

Este documento registra **o que foi encontrado**, **o que foi corrigido** e **o que foi verificado de fato**.
Nenhum item é declarado "pronto" sem teste executado; o que não pôde ser testado está marcado como **não verificado**.

---

## 1. Resumo

| Área | Situação antes | Situação agora |
|---|---|---|
| Geração de vídeos | Exportação direta no destino, sem validação; legendas gigantes em 9:16; vídeo de celular rotacionado lido como horizontal | Render em pasta temporária → validação real (ffprobe + decodificação completa) → publicação atômica versionada |
| Processamento | `BackgroundTasks` sem fila, sem cancelamento, sem recuperação; projeto preso em "transcrevendo" após reinício | Fila SQLite local com estados, progresso real, cancelamento, limite de concorrência, deduplicação e recuperação |
| Dados do projeto | Condição de corrida: exportação longa sobrescrevia edições feitas no intervalo | Bloqueio por projeto, gravação atômica com `fsync` e cópia `.bak` |
| Segurança | Qualquer site aberto no navegador podia enviar uploads (CSRF); sem proteção contra DNS rebinding; erros com caminhos locais | Middleware de Host/Origin/cabeçalho anti-CSRF, CSP restritiva, limite de corpo antes da leitura, mensagens sanitizadas |
| IA | Falhas de rede do Ollama derrubavam a análise; cortes temporais apareciam com nota; heurística por substring | Fallbacks explícitos por estado; corte temporal sem nota e identificado; seleção por frases completas com motivos; títulos da IA validados contra o texto |
| Direitos | Uma classificação geral | Seis categorias separadas, cada uma com estado, justificativa, verificação e ação; confirmação obrigatória antes de exportar com risco alto |
| Testes | 7 testes (1 ponta a ponta com vídeo sem áudio) | 68 testes (64 executados aqui com FFmpeg real; 4 da API FastAPI dependem do ambiente) |

---

## 2. Falhas encontradas (código original)

Severidade: **C** crítica · **A** alta · **M** média · **B** baixa. "Reproduzido" = confirmado executando o código original.

| # | Sev. | Onde | Problema | Evidência |
|---|---|---|---|---|
| 1 | C | `engine.export_video` | Legendas SRT renderizadas com `PlayResY` padrão do libass (288): em 1080×1920 a fonte ficava com ~120 px e um segmento virava um bloco de 7 linhas cobrindo o vídeo. | Reproduzido (quadro renderizado) |
| 2 | C | `engine.export_video` / `main.export_clip` | FFmpeg escrevia direto em `clips/clip_XX.mp4`: falha no meio deixava arquivo parcial publicado; código de saída 0 era o único critério de sucesso; nova exportação sobrescrevia a anterior (no Windows, falha se o arquivo estiver aberto no player). | Leitura do código |
| 3 | C | `main.export_clip`, `main.process` | *Lost update*: a exportação carregava `project.json`, executava o FFmpeg por minutos e regravava o JSON antigo, apagando ajustes salvos nesse intervalo. Não havia bloqueio entre threads. | Leitura do código; regressão coberta por teste |
| 4 | A | `main.py` | CSRF: `POST /api/projects` multipart é uma "requisição simples"; qualquer página aberta no navegador podia enviar arquivos ao servidor local. Sem validação de `Host` → DNS rebinding permitia a um site ler projetos e transcrições. | Leitura do código |
| 5 | A | `main.process` (BackgroundTasks) | Sem fila nem limite de simultaneidade (dois uploads = duas transcrições Whisper em paralelo); sem cancelamento; se o servidor fosse encerrado, o projeto ficava eternamente em `transcribing`. | Leitura do código |
| 6 | A | `engine.improve_with_ollama` | Só capturava `URLError/ValueError/KeyError/TimeoutError`: `ConnectionResetError`/`RemoteDisconnected` derrubavam a análise inteira. Uma falha no meio deixava parte dos cortes com nota da IA e o status dizia "mantida pontuação heurística". `urllib` respeitava `HTTP_PROXY`, podendo enviar transcrições a um proxy. | Reproduzido (`ConnectionResetError` propagado) |
| 7 | A | `engine.detect_scenes` | Só capturava `ImportError/RuntimeError`; erro do PySceneDetect ao abrir o vídeo derrubava toda a análise. | Leitura do código |
| 8 | A | `engine.probe_media` | Vídeo de celular com metadado de rotação (90°) era lido como 1280×720 horizontal. | Reproduzido |
| 9 | A | `engine.build_candidates` | Com transcrição sem candidatos válidos, o fallback era **o vídeo inteiro** como corte (ex.: 7200 s), que depois falhava na exportação (limite 900 s). | Reproduzido |
| 10 | A | `main.create_project` | Limite de tamanho verificado depois de o Starlette já ter gravado o upload inteiro na pasta temporária do sistema; apenas a extensão era validada; arquivo corrompido era aceito e falhava depois com `Command [...] returned non-zero exit status 1` (com caminhos locais). | Reproduzido (mensagem) |
| 11 | M | `engine.score_clip` | Termos de gancho por substring: `"mas "` casava em "for**mas** ", inflando a nota (68 vs. 64 para frase equivalente). | Reproduzido |
| 12 | M | `build_candidates` / UI | Cortes temporais recebiam nota fixa 35 e a descrição dizia "IA de transcrição não instalada" mesmo quando a causa era vídeo sem áudio ou falha da transcrição. | Leitura do código |
| 13 | M | `export_video` | Sem 1:1; 16:9 fixo em 1280×720 mesmo com fonte 4K; sem `setsar`; sem tratamento de taxa de quadros variável; trilha de áudio não mapeada (capa `attached_pic` poderia ser tomada como vídeo). | Leitura do código |
| 14 | M | `export_video` | Metadados do original (incluindo localização GPS de celulares) copiados para o corte publicado. | Leitura do código |
| 15 | M | `write_srt` | Segmentos longos do Whisper viravam um único bloco; `{…}` era interpretado pelo libass como comando; o fallback sem legendas só ocorria com `No such filter`. | Reproduzido (item 1) |
| 16 | M | `music.py` | Exceção engolida sem registro; sem pontuação/incerteza da correspondência; após erro de credencial continuava consultando (custo); amostra sem selecionar a trilha. | Leitura do código |
| 17 | M | `rights_review` | Vídeo, música, imagens, trechos de terceiros, reutilização e monetização misturados em um nível único. | Leitura do código |
| 18 | M | Scripts de início | Se o `pip install` falhasse, a `.venv` já existente impedia nova tentativa; sem verificação do FFmpeg; `pytest`/`httpx` fora dos requisitos. | Leitura do código |
| 19 | B | `engine.run` | `cwd=ROOT` fixo: caminhos relativos falhavam no ffprobe. | Reproduzido |
| 20 | B | `main.download_report` | Relatório regravado em disco a cada GET. | Leitura do código |
| 21 | B | `ClipEdit` | `NaN`/`Infinity` aceitos pelo Pydantic por padrão. | Leitura do código |
| 22 | B | Interface | Sem progresso real, sem cancelamento, sem recuperação de projetos anteriores; `/api/docs` exposto por padrão. | Leitura do código |

### Documentação × implementação

| Documentado | Realidade encontrada |
|---|---|
| README: "Legendas gravadas no vídeo… com FFmpeg/libass" | Funcionava, mas ilegível em 9:16 (item 1). |
| README: "vertical 9:16 … ou horizontal 16:9" | Correto, porém vídeos rotacionados eram mal classificados (item 8) e 1:1 não existia. |
| README: "seleção por falas transcritas" | Candidatos começavam em segmentos, mas não terminavam necessariamente em frase completa; heurística por substring. |
| EVOLUCAO: PySceneDetect | Instalado só com `requirements-ai`; falhas não tratadas (item 7). |
| README: fila robusta "não automatizada" | Correto; agora implementada. |

---

## 3. Correções e melhorias implementadas

### 3.1 Arquitetura (camadas)

```
app/
├── main.py          API FastAPI (camada fina) + tratamento de erros
├── security.py      Middleware ASGI: Host, Origin, anti-CSRF, limite de corpo, CSP
├── service.py       Regras de negócio (upload em fluxo, edição, exportação, relatório)
├── jobs.py          Fila persistente SQLite (estados, progresso, cancelamento, recuperação)
├── pipeline.py      Etapas de análise e exportação executadas pela fila
├── storage.py       project.json atômico, bloqueio por projeto, limpeza, espaço em disco
├── media.py         ffprobe/FFmpeg: validação, cenas, render, validação de saída
├── subtitles.py     Blocos de legenda, ASS dimensionado para a saída, SRT
├── transcription.py faster-whisper local (cache, fallback GPU→CPU, progresso)
├── selection.py     Seleção por frases, pontuação explicável, Ollama validado
├── rights.py        Classificação preventiva por categoria
├── music.py         ACRCloud opt-in
├── config.py / errors.py
└── engine.py        Compatibilidade com nomes públicos antigos
```

Decisão sobre SQLite: adequado — embutido no Python, transacional, sem serviço externo. Guarda tarefas, estados,
progresso e eventos por etapa. Os dados do projeto continuam em `project.json` (legível e portátil).

### 3.2 Importação

- O vídeo é gravado em fluxo direto na pasta do projeto (sem cópia na pasta temporária do sistema), com limite real
  aplicado durante o envio, verificação de espaço livre e `Content-Length` checado antes da leitura.
- O conteúdo real é validado com ffprobe **e** decodificação do início (erro) e do fim (aviso de arquivo incompleto).
- Detecta: ausência de trilha de vídeo, imagem de capa, rotação, VFR, várias trilhas de áudio, resolução/duração acima dos limites.
- Mensagens compreensíveis em português, sem caminhos locais. Falhas removem a pasta do projeto.

### 3.3 Exportação

- Formatos: **9:16**, **16:9**, **1:1** e **proporção original** (até 1920 px no lado maior; 4K reduzido).
- Enquadramento seguro sem cortar conteúdo: **quadro inteiro com barras** ou **quadro inteiro com fundo desfocado**.
  O reenquadramento por rosto não foi implementado (ver §5) — por isso nenhum modo corta a imagem.
- Corte preciso (`-ss` antes de `-i` com recodificação), CFR forçado quando a origem é VFR ou >60 fps,
  `aresample=async=1` para sincronia, trilha de áudio padrão mapeada explicitamente, AAC 48 kHz estéreo,
  normalização EBU R128 opcional, `+faststart`, metadados do original removidos.
- Validação automática do arquivo gerado: trilha de vídeo, resolução, codec/pixel format, taxa de quadros, duração
  (±3%), presença de áudio, sincronia aproximada (duração e início das trilhas), decodificação completa sem erros e
  aviso de trechos pretos. Só então o arquivo é publicado com `os.replace` e nome versionado (`clip_01_9x16_v2.mp4`).
- Legendas: arquivo ASS com resolução de saída, fonte e margens proporcionais, área segura acima da interface do
  TikTok/Reels/Shorts, até 2 linhas por bloco, sincronização por palavra quando disponível, escape de `{}`/`\`.
  Se a gravação das legendas falhar, o corte é exportado sem legendas com aviso explícito e o SRT continua disponível.
- Progresso real lido do `-progress` do FFmpeg; cancelamento encerra o processo; prioridade reduzida (nice / BELOW_NORMAL).

### 3.4 Inteligência artificial

- Transcrição: idioma `pt` por padrão, `word_timestamps`, VAD, `condition_on_previous_text=False` (reduz repetições
  alucinadas), descarte de segmentos com alta probabilidade de silêncio, cache do modelo, fallback para CPU,
  tratamento de memória insuficiente, progresso pelo tempo transcrito, áudio extraído pelo FFmpeg (respeita a trilha escolhida).
- Seleção: segmentos agrupados em frases; candidatos começam e terminam em frases; pontuação explicável
  (clareza, confiança da transcrição como indicador de qualidade de áudio, começo/fim coerentes, conectivo inicial,
  gancho nos primeiros 6 s, densidade de conteúdo, repetição, pausas longas, duração, mudança de cena);
  bloqueio de sobreposição (>20%) e de conteúdo repetido (similaridade >50%); ordenação dos melhores; motivos exibidos.
- Fala insuficiente, ausência de áudio, transcrição não instalada ou com falha → **modo temporal identificado**, sem nota.
- Ollama: verificação do serviço e do modelo antes de usar; prompt trata a transcrição como conteúdo não confiável
  (proteção contra *prompt injection*); JSON validado; título aceito só se suas palavras constarem no trecho;
  nota combinada (60% heurística + 40% IA); conexão direta a 127.0.0.1 sem proxy; tempo total limitado.

### 3.5 Direitos autorais e monetização

- Categorias independentes: vídeo original · música/trilhas · imagens/gráficos · trechos audiovisuais de terceiros ·
  conteúdo reutilizado (política de plataforma) · uso comercial/monetização.
- Estados: `declarado`, `revisar`, `risco_alto`, `nao_verificado`, `nao_aplicavel` — **não existe estado "livre"**.
- "Nenhuma correspondência musical" mantém o estado não verificado e diz explicitamente que isso não significa áudio livre.
- Monetização nunca é marcada como confirmada.
- Risco alto exige confirmação antes da exportação (registrada no relatório com data); a edição local não é bloqueada.
- ACRCloud: consentimento registrado, até 3 amostras de 12 s (configurável), interrupção imediata em erro de
  credencial/limite (evita custo), correspondências com pontuação e marcação de incerteza (<70), sem segredos em logs/respostas.
- Nada simula Content ID; nenhuma técnica de evasão (pitch, velocidade, espelhamento) foi implementada.

### 3.6 Segurança

- Bind 127.0.0.1 nos scripts; `Host` restrito a `127.0.0.1`/`localhost` (configurável por `AUTOCORTES_ALLOWED_HOSTS`).
- Requisições que alteram dados exigem `X-AutoCortes: 1` e `Origin` local; `Sec-Fetch-Site: cross-site` é recusado.
- CSP `default-src 'self'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Cache-Control: no-store` na API.
- Subprocessos sempre com lista de argumentos; filtros do FFmpeg não recebem caminhos do usuário (pasta de trabalho temporária).
- IDs validados por regex; arquivos servidos apenas por nomes gerados internamente; nome de download sanitizado.
- Erros técnicos sanitizados (sem caminhos nem tokens); detalhes ficam no log da tarefa.
- `/api/docs` desativado por padrão (`AUTOCORTES_API_DOCS=1` para ativar).
- **Acesso remoto não é suportado**: exigiria autenticação e TLS, que não foram implementados.

### 3.7 Fila e prevenção de travamentos

Estados `queued → running → done | failed | cancelled`, `cancelling` e `interrupted` (após encerramento inesperado,
com botão "Reprocessar"). Uma análise e uma exportação por vez por padrão (`AUTOCORTES_ANALYSIS_WORKERS`,
`AUTOCORTES_EXPORT_WORKERS`). Exportações idênticas em andamento são reaproveitadas. Temporários e uploads incompletos
são removidos na inicialização. Eventos e erros por etapa ficam no banco e aparecem na interface.

### 3.8 Interface

Envio com barra de progresso e arrastar-e-soltar; diagnóstico do sistema no topo (FFmpeg, libass, transcrição, Ollama,
disco); lista de projetos recentes; painel de etapas com progresso e cancelamento; tabela de direitos por categoria;
por corte: motivos da seleção, ajuste de início/fim (inclusive "usar posição atual do vídeo"), pré-visualização que
para no fim do trecho, formato, enquadramento, legendas, normalização, progresso/cancelamento da exportação e lista
de arquivos validados com download de MP4 e SRT. Layout sem rolagem horizontal em 390 px.

---

### 3.9 Narração com ChatGPT (adicionada após a auditoria)

Módulo `app/narration.py`. Roteiro pela Responses API com esquema JSON estrito, transcrição e contexto tratados como
dados não confiáveis, regra explícita de não inventar acontecimentos, limite de palavras pelo tempo do corte.
Voz pela TTS da OpenAI com cache, posicionamento sem sobreposição e mixagem com `sidechaincompress` (ducking).
Consentimento por pedido, chave só por variável de ambiente, erros mapeados sem expor a chave, aviso de voz sintética
(exigência da OpenAI) e lembrete de que narração não licencia material de terceiros.

## 4. Estado de verificação

### 4.1 Continuação em Windows — 21/10/2026 (v2.1.1)

Esta tabela complementa e prevalece sobre a tabela histórica abaixo para os itens repetidos.
Ambiente: Windows, Python 3.12.14, FFmpeg 9.0.2 (libass/libx264), FastAPI 0.159.1,
Starlette 1.9.0, httpx 0.28.1, faster-whisper 1.2.1 e PyAV 18.1.0.

| Item | Estado | Evidência |
|---|---|---|
| Suíte original sem alterações | Verificado | 81 passed, 29 subtests passed in 108.27s; `evidencias/baseline-native.txt` |
| Suíte ampliada | Verificado | 95 passed, 29 subtests passed in 103.79s; `evidencias/final-tests.txt` |
| API FastAPI real: raw, multipart, Range, segurança e narração | Verificado | Seis testes; Responses/TTS simulados apenas nas chamadas externas |
| Whisper real em CPU, modelo small, português | Verificado funcionalmente | Vídeo indicado de 74,51 s: 18 segmentos, 136 palavras em 38,44 s; `evidencias/whisper-metrics.json` |
| Qualidade semântica e alinhamento perceptual | Não verificado quantitativamente | Sem transcrição de referência e anotações de tempo manuais. Não calculado WER; pesos de seleção preservados |
| Limites dos timestamps e MP4 legendado do teste real | Verificado | Zero palavras/blocos fora dos limites; exportação aprovada por validate_output; quadro extraído |
| PyAV 19 + faster-whisper 1.2.1 | Falha reproduzida e corrigida | `metadata_errors` removido; fixar PyAV <19 resolveu; teste usa decoder real sem baixar modelo |
| Windows: acentos/espaços, cancelamento FFmpeg, arquivo bloqueado, concorrência | Verificado | Suíte de mídia e regressões com CreateFileW sem compartilhamento de exclusão |
| Windows: MP4 aberto em player nativo | Não verificado literalmente | Testado bloqueio de arquivo equivalente, e reprodução no Chrome; não foi usado player nativo |
| Windows: prioridade reduzida | Verificado na configuração | Teste confirma flag BELOW_NORMAL_PRIORITY_CLASS; prioridade observada no SO não medida |
| iniciar_windows.bat | Verificado com venv existente | Inicialização real em 127.0.0.1:8000, sem launcher py; log de inicialização |
| Instalação limpa via batch sem venv | Não verificado | Venv preparada manualmente com Python disponível no ambiente |
| Interface em 390 px e teclado | Verificado | Chrome headless real: largura 390/390, seletor por Enter, reprodução, zero erros de console; capturas e JSON |
| OpenAI: modelos e formatos de texto/imagem | Verificado documentalmente | Fontes oficiais em ENTREGA_ETAPA1.md; chamada real não realizada |
| OpenAI real | Não verificado | OPENAI_API_KEY ausente; casos offline/401/429 executados por simulação |
| Ollama real | Não verificado | GET local /api/tags retornou URLError: servidor indisponível |
| Linux nesta continuação / CI remota | Não verificado | Workflow adicionado, sem runner Linux nem repositório remoto fornecido |
| Funcionalidades das etapas 2–5 | Pendente | Não implementadas nesta entrega; apenas workflow CI preparado |

### 4.2 Evidências históricas da auditoria anterior

Ambiente desta auditoria: Linux, Python 3.13, FFmpeg 6.1.1 com libass e libx264. Sem acesso ao PyPI.

| Funcionalidade | Estado | Como foi verificado |
|---|---|---|
| Leitura de MP4, MOV, M4V, MKV, AVI (MPEG-4/MP3), WEBM (VP8/Opus) | ✅ Verificado | `tests/test_media.py` com arquivos gerados |
| Vertical, horizontal, quadrado, rotação de celular, 4K, sem áudio, VFR | ✅ Verificado | `test_media.py`, `test_service.py` |
| Arquivo corrompido, truncado, vazio, só áudio, extensão falsa | ✅ Verificado | `test_media.py`, `test_service.py` |
| Nomes e pastas com acentos, espaços e símbolos | ✅ Verificado | `test_media.py` |
| Exportação 9:16 / 16:9 / 1:1 / original, barras e fundo desfocado | ✅ Verificado | Validação automática + inspeção visual de quadros |
| Validação de saída (duração, resolução, codec, áudio, sincronia, decodificação) | ✅ Verificado | `test_media.py` (inclui rejeição de saída incorreta) |
| Legendas (2 linhas, área segura, acentos, escape) e fallback sem legendas | ✅ Verificado | Testes + inspeção visual em 9:16 e 16:9 |
| Cancelamento de exportação/análise sem arquivo parcial | ✅ Verificado | `test_media.py`, `test_service.py` |
| Fila, deduplicação, recuperação após reinício, `.bak` de projeto corrompido | ✅ Verificado | `test_service.py` |
| Edição concorrente durante exportação sem perda | ✅ Verificado | `test_service.py` (regressão do item 3) |
| Middleware de segurança (Host, Origin, CSRF, limite, cabeçalhos) | ✅ Verificado | Testes unitários + servidor real com curl |
| Interface (envio, progresso, direitos, confirmação, exportação, mobile) | ✅ Verificado* | Chromium automatizado contra servidor Starlette com as mesmas rotas e a mesma camada de serviço |
| Seleção por frases, ranking, sem duplicatas, fallback honesto | ✅ Verificado | `test_engine.py` (transcrição sintética) |
| Ollama desligado / JSON inválido / título inventado | ✅ Verificado | `test_engine.py` (respostas simuladas) |
| Narração: roteiro, edição, cache de voz, mixagem com ducking, legendas da narração, consentimento | ✅ Verificado* | `tests/test_narration.py` com API OpenAI simulada + FFmpeg real; fluxo completo no Chromium |
| Narração com a API real da OpenAI (qualidade do roteiro/voz, nomes de modelos) | ⚠️ Não verificado | Sem chave no ambiente. Modelos configuráveis por `OPENAI_MODEL`/`OPENAI_TTS_MODEL`. |
| Rotas FastAPI (`app/main.py`) | ⚠️ Não verificado aqui | `tests/test_api.py` escrito; FastAPI não pôde ser instalado no ambiente. Rode `pytest` após `pip install -r requirements-dev.txt`. |
| Transcrição real com faster-whisper (qualidade PT-BR, GPU) | ⚠️ Não verificado | Pacote indisponível no ambiente; fluxo testado com transcrição simulada. |
| Ollama real (qualidade dos títulos/notas) | ⚠️ Não verificado | Sem servidor Ollama no ambiente. |
| ACRCloud real | ⚠️ Não verificado | Exige credenciais e conta paga; falhas e ausência de configuração testadas. |
| Windows 10/11 | ⚠️ Não verificado | Código preparado (argumentos sem shell, filtros com caminho relativo, nomes versionados, prioridade baixa), mas não executado em Windows. |

\* Verificado com um servidor de teste que reproduz as rotas de `main.py`; a interface consome exatamente os mesmos endpoints.

---

### 4.3 Revisão da v2.1.1 e entrega da v2.2 — 21/10/2026

**Revisão das mudanças do Codex (v2.1.1).** O diff foi lido arquivo a arquivo; todas as mudanças foram consideradas
corretas e mantidas:

- bloqueio na publicação versionada;
- *snapshot* do roteiro e consentimentos persistidos;
- `store: false` nas chamadas à OpenAI;
- validação estrita das respostas do Ollama e da OpenAI;
- sanitização ampliada;
- Origin malformada e Content-Length negativo tratados;
- fim de legenda limitado ao corte;
- launcher e CI.

Problemas encontrados e corrigidos na revisão:

- pipes do FFmpeg não eram fechados (os avisos `ResourceWarning` apareceram nos testes);
- `media.py` com quebras de linha CRLF, divergindo do restante.

**Verificação no ambiente Linux desta revisão:**

| Verificação | Resultado |
|---|---|
| Suíte `unittest` (`python -m unittest discover -s tests -t .`) | 98 testes executados: 91 aprovados e 7 pulados (os da API FastAPI, indisponível neste ambiente); nenhuma falha |
| `tests/test_regressions_v21.py` | 10 aprovados e 3 pulados (2 exigem Windows e 1 o faster-whisper), via substituto mínimo de pytest |
| Testes de automação (`tests/test_automation.py`) | 15 aprovados, cobrindo os itens abaixo |
| Interface com servidor de teste e as mesmas rotas | Fluxo abaixo executado no Chromium, sem erros de console e sem rolagem horizontal em 390 px |
| `tests/test_api.py` (inclui o novo teste de automação) | **Não executado aqui**; deve ser rodado com `pytest` em ambiente com FastAPI |

Os testes de automação cobrem:

- pasta de entrada (com arquivo parcial, corrompido e duplicado);
- piloto com dois formatos e pasta de saída;
- pausa e retomada por risco alto de direitos;
- narração automática com falha transitória e nova tentativa;
- ausência de chave da OpenAI;
- Exportar todos;
- migração do banco de tarefas;
- encurtamento de pausas com validação de sincronia.

O fluxo executado no Chromium foi: salvar Configurações → enviar vídeo → piloto concluído com 4 arquivos validados e
copiados para `saida/` → pasta de entrada importando um segundo vídeo.

**Não verificado:** piloto e pasta de entrada no Windows, onde o comportamento de bloqueio de arquivo durante a cópia
pode diferir (o código só reivindica o arquivo depois que `os.replace` funciona). Também não verificados: narração
automática com a OpenAI real e uso contínuo por várias horas.

## 5. Não implementado (e por quê)

| Item | Motivo / próximo passo |
|---|---|
| Reenquadramento inteligente por rosto/falante | Requer modelo de visão (ex.: MediaPipe) e revisão manual; até lá só modos que não cortam a imagem. |
| Remoção automática de silêncios | **Parcial na v2.2:** "Encurtar pausas longas" é opcional por exportação e não se combina com narração. Ainda falta pré-visualização antes de exportar. |
| Timeline visual quadro a quadro, desfazer/refazer | Exige reescrever o editor (o plano de evolução sugere React). |
| Upload ao YouTube (OAuth) e catálogo local de impressões digitais | Fora do escopo de correção; o relatório já separa o que é verificável. |
| Instalador Windows | Fora do escopo; os scripts de início foram corrigidos. |
| Limite de VRAM/GPU por tarefa | A fila limita a simultaneidade; o Whisper usa um modelo por vez com fallback para CPU. |

## 6. Riscos remanescentes

- A heurística de seleção mede estrutura (frases completas, gancho, densidade), não o interesse real do assunto; o Ollama ajuda, mas a revisão humana continua necessária.
- A sincronia de legendas depende dos tempos do Whisper; sem tempos por palavra, a divisão em blocos é proporcional ao texto.
- A classificação de direitos depende das declarações do usuário; não substitui análise jurídica.
- A validação usa decodificação completa do corte (custo ≈ tempo de leitura do arquivo); para cortes longos em 4K isso acrescenta alguns segundos.
