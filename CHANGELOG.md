## Não lançado

### Player: pular 10 s e 30 s
- Botões **-30**, **-10**, **+10** e **+30** ao lado do voltar/avançar 1 s; atalhos **J** / **L** (10 s) e
  **Shift+J** / **Shift+L** (30 s), também na ajuda de teclado.

### "Só o streamer": área desenhada à mão
- **Desenhar área no vídeo** (aba Efeitos, com "Só o streamer", "Webcam + jogo" ou "Por trecho"): arraste um
  retângulo sobre o player; a área fica no corte e a exportação recorta só ela, com fundo desfocado, sem detecção
  automática nem análise de rostos. Nos trechos "seguir rosto" do "Por trecho" os rostos continuam sendo seguidos.
  "Usar a automática" volta para a detecção. `PUT /api/projects/{id}/clips/{n}/focus`.
- "Só o streamer" num trecho do "Por trecho" em formato horizontal passa a usar a janela (antes seguia o rosto).

### Por trecho: tempos editáveis
- Cada trecho tem início e fim em campos de texto (`52.5`, `0:52.5`); mudar um limite move o do vizinho junto,
  com mínimo de 0,5 s por trecho. Focar o campo leva o vídeo até o ponto; Enter confirma, Esc desfaz.

### Legenda: sem repetições e com correção à mão
- **Legenda precisa sem o contexto da frase anterior:** com ele, numa live barulhenta o modelo entrava em laço e
  repetia a mesma frase por cima da fala real ("Porra, viado!" 8× no lugar de "Ô louco! … Eu ia comer teu bolo!"
  no corte #10), e levava 67 s em vez de 15 s. Cortes refinados com a configuração antiga são refeitos na próxima
  prévia ou exportação (marca `large-v3-turbo:v2`).
- **Corrigir texto da legenda** (aba Legendas): lista as frases do corte com o texto editável; clicar no tempo leva
  o vídeo até a frase; texto vazio tira a frase. As palavras ganham tempo de novo para a legenda animada, e as frases
  corrigidas ficam marcadas: a legenda precisa não as desfaz. Erros de áudio que nenhum ajuste corrige ("pra ele dar
  sem deficiência" no lugar de "pra acender aqui", com a torcida ao fundo) se resolvem aqui.
  API: `GET`/`PUT /api/projects/{id}/clips/{n}/subtitles`.

### Render mais rápido e "Só o streamer" em transmissões
- **Exportação com pausas cortadas** (plano de edição ou "encurtar pausas"): a leitura da origem para no fim do corte.
  Antes, a saída ficava mais curta que o trecho e o FFmpeg seguia decodificando o resto da live até preencher a
  duração (a barra parava em ~85%). Corte de 108 s de uma live: 1060 s → 63 s; prévia: ~30 min → 60 s.
- Fundo desfocado: escurece antes de ampliar e amplia em bilinear (mesmo resultado, ~13% menos tempo no ramo).
- **"Só o streamer"**: a janela da webcam é procurada só nas laterais (rostos no meio da tela não servem de ponto
  de partida). Em transmissões sem webcam, a **janela dos streamers no canto** (picture-in-picture) é achada pela
  moldura fixa, formato 16:9 e vídeo dentro; nas cenas fora da bancada em que ela aparece, o recorte é o vídeo de
  dentro, ampliado com fundo desfocado. Corte #10 do vídeo IRL: 50 s de campo passaram de jogadores para os
  streamers. Planos de enquadramento em cache são refeitos (versão 8).

### Seguimento — limpeza de cache
- Painel de mídia: "Liberar cache (tamanho)" quando o projeto tem 1 MB ou mais de cache. Apaga miniaturas, onda do
  áudio, energia, faixas pretas, planos de enquadramento e prévias (`cache/`, `direction/`, `preview/`), que o app refaz
  quando precisa; vídeo, transcrição, cortes e exportações ficam. Recusa com tarefa em andamento no projeto.
- API: `GET /api/projects/{id}/cache` (tamanho por parte) e `DELETE /api/projects/{id}/cache`.

### Seguimento — avaliação independente
- README descreve feedback em segundo vídeo de podcast/IRL, comparação v1 com/sem áudio, caminhos temporários e limitações do recall; não altera pesos.


### Seguimento — cobertura de concorrência e caminhos
- Testes simultâneos de análise/refinamento verificam uma única construção do modelo GPU compatível; CPU e tipos de cálculo diferentes permanecem separados.
- Testes rejeitam UNC, dispositivos e streams antes de acessar arquivos; destinos de links são simulados sem exigir privilégios de symlink no Windows.


### Seguimento — mensagens de acesso
- Studio solicita senha em 401 `auth_required`, mantém credencial somente na memória e limita a uma nova tentativa; cancelar não repete a operação.
- API e upload mostram orientação para respostas 413, inclusive HTML de proxies/túneis. Não habilita acesso de rede nem implementa os PRs de LAN/Cloudflare.


### Seguimento — correções verificadas
- Importação local valida vídeo real e destino canônico antes do fingerprint; bloqueia rede, dispositivos e streams.
- Timeline calcula escala com o editor visível; perfis mostram opções efetivas do plano e deixam de indicar um perfil incompatível.
- Inicializador testa a resposta da NVIDIA e reinstala dependências quando o hash está ausente ou mudou; só grava marcadores após sucesso.
- Mensagem NVENC informa que desempenho, compressão e tamanho dependem do conteúdo, sem promessa universal de qualidade idêntica.


- Fase 4 de melhorias:
  - **Perfis de exportação:** formato, enquadramento, legenda, filtro, volume e gancho de uma vez. Quatro prontos por
    tipo de conteúdo (Live de jogo, IRL / rua, Podcast / entrevista, YouTube horizontal) e até 20 salvos pelo
    usuário; lembrado por projeto; "Tipo de conteúdo" ao criar o projeto já deixa o perfil escolhido.
  - **"Só o streamer" no "Por trecho"** numa transmissão sem janela de webcam: esses trechos seguem rostos só na cena
    principal (bancada), como o modo "Só o streamer"; os de "seguir rosto" continuam seguindo todos.
  - **Inicializador Windows:** instala também transcrição e rostos (`requirements-ai.txt`) e, com placa NVIDIA, as
    bibliotecas de CUDA (`requirements-gpu.txt`); `AUTOCORTES_MINIMAL=1` para só o básico; mostra o comando do
    `winget` quando falta o FFmpeg. Antes, uma instalação nova ficava sem transcrição.
  - **Motor:** custo da camada de áudio medido com a máquina parada (sem custo mensurável na seleção); camada visual
    (movimento na tela) medida e **não integrada**: os cortes aprovados são mais calmos, mas o reforço piorou o
    ranking na validação cruzada (−0,03 de recall). Relatório em `ANALISE_MOTOR_CORTES.md` (seção 9).
  - Documentos: `REVISAO_PRS_ANTIGOS.md` (revisão dos PRs #2, #3 e #4) e `ESTUDO_INSTALADOR_WINDOWS.md`.

- Fase 3 (desempenho e confiabilidade):
  - **Faixas pretas:** leituras do vídeo inteiro em cache e as do trecho em paralelo. Live 1080p60: 35 s → 11 s na
    primeira exportação do vídeo e 5,3 s nas seguintes.
  - **Exportação pela placa de vídeo (NVENC)**, com volta automática para o processador se a placa falhar. CQ 28 até
    30 fps e 30 a 60 fps, com B-frames, calibrados para SSIM/PSNR iguais ou acima do x264 CRF 20. Corte de 62 s:
    82–95 s → 30–51 s, arquivo cerca de 29% maior. Desliga em Configurações ou com `AUTOCORTES_ENCODER=x264`.
  - **Um só modelo Whisper na placa:** a análise e a legenda precisa reaproveitam o mesmo `large-v3-turbo`
    (antes, duas cópias: cerca de 1 GB a menos de memória de vídeo).
  - **Importar do computador sem enviar:** cole o caminho do vídeo; no mesmo disco da pasta do AutoCortes vira um
    hard link (instantâneo, sem ocupar espaço); em outro disco, cópia local. Avisa se o vídeo já está num projeto.
  - **Feedback nos cortes:** 👍 Bom / 👎 Rejeitar, "Esconder rejeitados" e "Por que este corte foi sugerido"
    (evidências e alertas do motor). As marcações ficam num registro do projeto que sobrevive a refazer a seleção;
    `scripts/avaliar_selecao.py --feedback` mede o motor contra elas.
  - **Tentar de novo** em exportações com erro (mesmas opções, tempos e título atuais do corte), no painel de
    exportações e em Processamentos.
  - **Inicializador Windows:** abre o navegador só quando o servidor responde, mostra o que verificar quando ele
    para (porta em uso, dependências) e reinstala as dependências quando o `requirements.txt` muda.

- Interface, fase 2 (fluxo de trabalho):
  - **Exportar selecionados:** caixa de seleção em cada corte, "Todos" e ordem (melhores primeiro ou ordem do vídeo);
    o lote escolhe destinos (Shorts, Reels, TikTok, Horizontal, Original), mostra quantos arquivos e o tamanho
    estimado, avisa cortes longos demais para Shorts/Reels/TikTok e usa as opções do editor. Shorts, Reels e TikTok
    geram um único arquivo 9:16. A Revisão de direitos continua exigindo confirmação.
  - **Progresso do lote:** "Lote: 4 de 12 prontos · renderizando corte 3 de 6" e "Corte 3 de 6" em cada exportação.
  - **Abrir pasta:** no painel de exportações (pasta do projeto) e na página inicial (pasta de saída).
  - **Vídeo duplicado:** antes do envio, o navegador calcula uma impressão rápida (tamanho + SHA-256 de 3 blocos de
    4 MB) e o app avisa se o vídeo já está num projeto, com atalho para abri-lo e confirmação para importar de novo.
  - Exportar 18 cortes: de 36 cliques (um por um) para 3 (Todos, Exportar selecionados, Exportar).

- Motor de cortes, fase 3 (camada de áudio, `app/audio_events.py`):
  - Energia do áudio do vídeo inteiro (FFmpeg, 0,2 s, em cache por arquivo) e eventos com evidência: pico de volume,
    possível risada/reação, silêncio, áudio estourado, fala acelerada.
  - Reforço na seleção só para trechos com conteúdo que terminam com reação sonora; o fim do corte inclui a risada
    logo depois da última frase. Alertas de áudio estourado e silêncio longo.
  - Live de 8,5 h: Recall@18 0,28 → 0,33 e Recall@50 0,50 → 0,56 (ganho otimista: a regra foi escolhida nessa mesma
    referência). `AUTOCORTES_AUDIO_LAYER=0` desliga.
  - Verificação de troca de cena por busca binária na pontuação das janelas.

- Motor de cortes, fase 2 (ver ANALISE_MOTOR_CORTES.md):
  - **Quantidade automática relativa ao vídeo:** até 4 cortes por hora (mínimo 3), configurável por
    `AUTOCORTES_AUTO_CLIPS_PER_HOUR`. Na live de 8,5 h: 34 cortes em vez de 170, com o mesmo Recall@18 (0,28) e
    Recall@30 (0,50) da linha de base.
  - **Curadoria com IA somada à heurística:** quando a IA funciona, as duas listas são unidas (trechos sobrepostos
    viram um corte com as duas origens em `sources`); antes a lista da heurística era descartada.
  - Cada corte traz `sources`, `evidence`, `warnings`, `confidence`, `editorial_score` e `technical_score`
    (os dois últimos só no motor v2).
  - **Motor v2 experimental** (`AUTOCORTES_SELECTION_ENGINE=v2`): nota editorial × portão técnico, assunto sustentado,
    pergunta e resposta no mesmo corte, reação depois do desfecho, pausas de live toleradas. Não superou o v1 na
    referência de 18 cortes, por isso o padrão continua v1.
  - `scripts/avaliar_selecao.py`: avaliação reproduzível (Recall@K, erro de limites, duplicação) contra cortes de
    referência.

- Corrigido: **cancelar uma exportação no Windows** só parava quando a renderização terminava. O FFmpeg instalado por
  Chocolatey/Scoop é um "shim" que executa o `ffmpeg.exe` real como filho, e o cancelamento encerrava só o shim. Agora
  a árvore inteira é encerrada (`taskkill /T` no Windows, grupo de processos no Linux/macOS) e o término não depende
  mais do fim do pipe de progresso. Teste determinístico em `tests/test_process_control.py`.
- Corrigido: no Studio, a **linha do tempo** não posicionava régua, quadros e cortes, porque a política de segurança
  (CSP `style-src 'self'`) bloqueia atributos `style` em HTML gerado. As posições agora são aplicadas pelo CSSOM.
- Studio: botão de **cancelar exportação** com barra de progresso, zoom que encaixa o vídeo inteiro, atalhos N/S/Delete,
  arrastar e soltar vídeo, editor primeiro no celular, estados de carregamento/erro/sucesso e ação única de exportar.
- CI: o `push` roda só no `master` (o `pull_request` cobre os branches com PR), execuções antigas do mesmo PR são
  canceladas e cada job tem tempo limite.

- FilmCraft: etapa opcional **Remover pausas longas** (`transcript.set` + `transcript.removePauses`), com legendas
  recriadas da transcrição cortada e validação estrutural da linha do tempo. Ver `FILMCRAFT-PROTOTIPO.md`.
- Corrigido: o **gancho** (frase no topo, primeiros 3 s) saía cortado nas bordas do vídeo porque o cabeçalho ASS usa
  `WrapStyle: 2` (sem quebra automática). O evento do gancho agora liga a quebra do libass (`\q0`), corta em palavra
  inteira e divide palavras gigantes. Regressão testada com render real (FFmpeg/libass) em 9:16, 16:9 e 1:1.
- Corrigido: **títulos** truncados com "…" no meio da frase. O limite subiu de 70 para 80 caracteres e frases longas
  são cortadas numa fronteira natural (vírgula, travessão ou conjunção), sem reticências; só sem fronteira cai para
  palavra inteira com "…", nunca terminando em artigo, preposição ou conjunção.
- Windows: `iniciar_windows.bat` agora prefere o **Python 3.12** (`py -3.12`) e aceita 3.10 a 3.12. Antes ele usava `py -3` (a versão mais nova), e com o Python 3.15 a instalação das dependências falhava (o `pyyaml` não tem pacote pronto). Se não houver versão suportada, ou se a `.venv` existente for de outra versão, mostra a versão encontrada e como instalar o 3.12 (`py install 3.12`), sem apagar nada. O arquivo passou a usar só CRLF e ASCII. Testado em `tests/test_windows_launcher.py`.

## 2.4.0 — 2026-10-23

- Central de automação com disponibilidade de IA, perfis e fila persistente.
- Importação opcional de vídeos públicos YouTube/Shorts via yt-dlp, cancelamento e nova tentativa.
- Perfil congelado por projeto; consentimento revogado respeitado nas novas etapas automáticas.
- Linha de pré-visualização dos cortes e pacote ZIP de exportações.
- FilmCraft opcional integrado à fila e legendas derivadas da transcrição.
- Bloqueio de segunda instância na mesma pasta de dados.
- Extras links e instaladores de automação com dependências travadas.

# Changelog

## 2.3.1 — 22/10/2026 (estabilidade e instalação)

- SHA-256 do vídeo completo em blocos; migração dos fingerprints parciais antigos ao comparar duplicatas.
- Identificador por execução do piloto, propagado às tarefas de roteiro e exportação. Resultados antigos não alteram a execução atual.
- Falhas de roteiro sem recuperação e exportações planejadas ausentes impedem status de sucesso. Fallback concluído continua sendo sucesso.
- Conclusão aguarda submissões e callbacks de narração; callbacks pendentes podem ser recuperados após reiniciar sem repetir exportações já registradas.
- Callback de cancelamento executado fora do lock SQLite, evitando inversão de locks com os projetos.
- pyproject.toml, uv.lock, Python 3.12 como padrão e requirements exportados para compatibilidade com os lançadores existentes.
- Dockerfile para CPU, Compose limitado a localhost, volumes persistentes para projetos/modelos e healthcheck. Workflow de smoke test do contêiner incluído; execução local do Docker ainda não validada.
- Testes de regressão para essas correções e atualização do CI para usar o lockfile.

Detalhes e limites de validação desta entrega estão em VALIDACAO-v2.3.1.md.

## 2.3.0 — 22/10/2026 (fusão com o Clipper)

Incorpora ao AutoCortes as três partes do protótipo Clipper que o app ainda não tinha. O restante do Clipper
(transcrição, seleção por frases, render) já existia aqui em versão mais robusta e não foi duplicado.

### Adicionado
- **Enquadramento "Seguir rosto"** (`framing: "track"`, módulo `reframe.py`): recorte 9:16 ou 1:1 que acompanha o
  rosto de quem fala. Detecção local com classificadores Haar do OpenCV (incluídos em `app/data`, nada é baixado),
  estimativa do falante pelo movimento da boca, câmera virtual com zona morta e suavização reiniciada a cada mudança de
  cena. Cenas sem rosto usam fundo desfocado só naquele intervalo. O caminho da câmera vira uma expressão de tempo do
  filtro `crop`, então a exportação continua 100% no FFmpeg (validação, progresso e cancelamento inalterados). Sem
  OpenCV ou sem rosto, exporta com fundo desfocado e avisa.
- **Legenda dinâmica** (`subtitle_style: "dinamica"`): até 3 palavras por vez, caixa alta, palavra falada destacada;
  usa o tempo de cada palavra e é remapeada quando as pausas são encurtadas. O SRT continua em frases.
- **Frase de gancho** (`hook: true`): texto curto no topo nos 3 primeiros segundos (gancho da curadoria ou o título).
- **Curadoria com IA na nuvem** (módulo `curation.py`, opt-in nas Configurações): OpenAI ou Anthropic propõem os
  trechos e um passo "juiz" dá notas de gancho, coerência, valor e tendência, com comentários. Nota final = 70% IA +
  30% heurística local. Envia só texto da transcrição, exige consentimento registrado com data, trata a transcrição
  como não confiável e descarta títulos/ganchos sem respaldo no trecho. Qualquer falha mantém a seleção heurística.
  Resumo e hashtags da curadoria entram no pacote de publicação.
- Configurações: provedor e foco da curadoria; estilo de legenda e gancho no piloto automático.
- Interface: "Seguir rosto", estilo de legenda e gancho por corte e em "Exportar todos"; avaliação da IA em
  "Por que este trecho?".
- `/api/health` informa `curation` (provedores configurados) e `face_tracking`.
- 24 testes novos (câmera virtual, expressão do crop avaliada, exportação real com rosto simulado, legendas
  dinâmicas gravadas, curadoria com OpenAI/Anthropic simuladas, consentimento e fluxo completo pela fila).

### Variáveis novas
`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (padrão `claude-sonnet-5-5`), `CURATION_OPENAI_MODEL` (padrão: `OPENAI_MODEL`).

## 2.2.0 — 21/10/2026 (modo autônomo)

### Adicionado
- **Configurações persistentes** (`data/settings.json`, sem segredos nem caminhos): declarações padrão de direitos,
  perfil do piloto automático, narração automática, pastas de entrada e saída. `GET`/`PUT /api/settings`.
- **Piloto automático**: escolhe os melhores cortes (nota mínima, máximo por vídeo, temporais opcionais) e exporta nos
  formatos configurados, opcionalmente com roteiro e voz da OpenAI. Pausa em risco alto de direitos até o usuário
  confirmar (`POST …/autopilot/resume`). Pode ser rodado em projetos já analisados (`POST …/autopilot/run`).
  O perfil é congelado no envio e cada decisão fica registrada no projeto.
- **Pasta de entrada monitorada**: importa arquivos estáveis, ignora parciais, detecta duplicatas por impressão digital,
  move para `processados/`, ou para `falhas/` com `.erro.txt`, e recupera arquivos presos após encerramento inesperado.
- **Pasta de saída e pacote de publicação**: cópia atômica de MP4, SRT e texto com título, descrição e hashtags
  extraídos do conteúdo, mais as pendências de direitos e o aviso de voz por IA. `GET …/clips/{id}/publication`.
- **Exportar todos** (`POST /api/projects/{id}/export-all`).
- **Encurtar pausas longas** (opção por exportação): usa os tempos das palavras da transcrição ou o silencedetect, com
  intervalos alinhados aos quadros e legendas remapeadas; desativado quando há narração.
- **Novas tentativas automáticas** na fila para falhas transitórias (OpenAI fora do ar, limite de uso, rede), com espera
  crescente, e migração automática do banco de tarefas da v2.1.
- Interface: diálogo de Configurações, opção de piloto no envio, painel do piloto com eventos, Exportar todos, opção
  Encurtar pausas, links do pacote de publicação e status da pasta na biblioteca.
- 15 testes de automação e 1 teste de API novos.

### Corrigido
- `run_ffmpeg` não fechava os pipes do processo (vazamento de descritores em uso contínuo).
- `media.py` e `test_regressions_v21.py` normalizados para LF (estavam com CRLF misturado).
- Os testes não escrevem mais em `entrada/` e `saida/` reais.

## 2.1.1 — 21/10/2026 (continuação, etapa 1)

- Executada a suíte original no Windows: 81 testes aprovados, incluindo os cinco da API antes pendentes.
- Corrigida incompatibilidade real do faster-whisper com PyAV 19; faixa compatível registrada nos requisitos AI.
- Publicação de versões de exportação protegida contra concorrência no mesmo diretório.
- Exportação guarda snapshot do roteiro; alterações posteriores geram tarefa distinta. Roteiro gerado
  para tempos antigos não sobrescreve o corte editado durante a chamada externa.
- Consentimentos OpenAI persistidos antes da fila e incluídos no relatório; autorização separada de TTS na UI.
- Responses usa `store: false`; respostas malformadas e tempos não finitos geram erro seguro.
- Ollama exige tipos corretos de nota e booleano. Legendas terminam dentro do corte.
- Origins malformadas e Content-Length negativo rejeitados; sanitização remove chaves e caminhos adicionais.
- Leitura de stderr do FFmpeg sem acumular lista proporcional à duração do processamento.
- Launcher Windows reutiliza venv e aceita `python` quando `py` não existe; modo de diagnóstico sem navegador.
- Seletor de arquivo acessível por teclado; confirmação de direitos reiniciada ao trocar projeto; favicon local.
- 14 testes adicionais: total 95; validação real de Whisper, FFmpeg, arquivo bloqueado no Windows e Chrome em 390 px.
- CI configurada em Windows e Linux para Python 3.10/3.12; ainda não executada remotamente.

## 2.1.0 — 21/10/2026 (narração com ChatGPT)

### Adicionado
- Narração opcional por corte: roteiro gerado pelo ChatGPT (Responses API com saída JSON validada) nos estilos
  recap, comentário ou documentário, com contexto do usuário e envio opcional de quadros do vídeo.
- Editor de roteiro na interface (tempos e texto por bloco) e sugestão de título.
- Voz com OpenAI TTS (`gpt-4o-mini-tts`, voz configurável), cache local por texto/voz, aceleração leve (até 1,25×)
  quando um bloco não cabe, mixagem com ducking automático do áudio original e legendas da narração.
- Consentimento explícito para envio à OpenAI, aviso obrigatório de voz gerada por IA e lembrete de que narração não
  licencia material de terceiros.
- Endpoints `POST/PUT /api/projects/{id}/clips/{clip}/narration`; opções de exportação `narration`, `voice`,
  `original_volume`, `subtitle_source`.
- 12 testes novos (API OpenAI simulada, mixagem real com FFmpeg).

## 2.0.0 — 21/10/2026 (auditoria e correções)

### Corrigido
- Legendas gigantes em 9:16 (até 7 linhas cobrindo o vídeo): agora ASS na resolução de saída, até 2 linhas, área segura.
- Exportação publicada sem validação e sobrescrevendo arquivos: render temporário → validação real → publicação atômica versionada.
- Perda de edições quando uma exportação longa regravava `project.json` antigo (bloqueio por projeto + gravação atômica).
- CSRF e DNS rebinding na interface local.
- Falhas de rede do Ollama e da detecção de cenas derrubavam a análise inteira.
- Vídeos de celular rotacionados lidos como horizontais.
- Fallback que gerava um corte do vídeo inteiro (ex.: 2 h) e falhava na exportação.
- Upload: limite aplicado só depois da cópia completa para a pasta temporária; conteúdo não validado; mensagens com caminhos locais.
- Heurística por substring ("mas" dentro de "formas"); cortes temporais exibidos com nota.
- Metadados do original (GPS) copiados para os cortes.
- Scripts de início não refaziam a instalação após falha do `pip`.

### Adicionado
- Fila local persistente (SQLite) com progresso real, cancelamento, limite de concorrência, deduplicação e recuperação após reinício.
- Formatos 1:1 e proporção original; enquadramento com fundo desfocado; 16:9 em 1080p quando a fonte permite; normalização EBU R128 opcional.
- Validação automática do MP4 (duração, resolução, codec, fps, áudio, sincronia, decodificação, trechos pretos).
- Seleção por frases completas com pontuação explicável, motivos exibidos, bloqueio de sobreposição e de conteúdo repetido.
- Validação das respostas do Ollama (JSON, nota, título com respaldo no texto) e proteção contra *prompt injection*.
- Revisão de direitos em seis categorias com estado, justificativa, verificação e ação; confirmação obrigatória para risco alto.
- ACRCloud: pontuação e incerteza das correspondências, mensagens por código de erro, parada em erro de credencial/limite.
- Interface: progresso de envio e de etapas, cancelar/reprocessar, projetos recentes, diagnóstico do sistema, exportações por formato com download de MP4/SRT.
- Endpoints: `GET /api/projects`, `DELETE /api/projects/{id}`, `POST /api/projects/{id}/reanalyze`, `GET /api/jobs/{id}`,
  `POST /api/jobs/{id}/cancel`, `GET …/clips/{id}/subtitles`.
- 68 testes automatizados com vídeos gerados por FFmpeg.

### Alterado
- `app/engine.py` dividido em módulos; nomes antigos mantidos por compatibilidade.
- Exportação agora é assíncrona: `POST …/export` retorna uma tarefa; o arquivo fica disponível em `…/download?format=9:16`.
- Requisições que alteram dados exigem o cabeçalho `X-AutoCortes: 1`.
- Detecção de cenas via FFmpeg; `scenedetect` deixou de ser necessário.
- Python mínimo: 3.10.
