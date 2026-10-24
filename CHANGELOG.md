## Não lançado

- **Acesso pela internet via Cloudflare Tunnel**: `publicar_cloudflare_windows.bat` abre o app (ainda em 127.0.0.1)
  e o `cloudflared`, em modo rápido (`*.trycloudflare.com`, sem conta) ou com túnel fixo no seu domínio
  (`CLOUDFLARE_TUNNEL_TOKEN` + `AUTOCORTES_PUBLIC_HOST`). Senha obrigatória. `AUTOCORTES_ALLOWED_HOSTS` aceita
  sufixos (`.trycloudflare.com`); o bloqueio por senha errada usa o IP real (`CF-Connecting-IP`) quando a conexão
  vem do `cloudflared` local; envio acima de 100 MB recusado pela Cloudflare mostra aviso claro. Ver `CLOUDFLARE.md`.
- **Acesso por IP**: `python -m app` com `AUTOCORTES_HOST`/`AUTOCORTES_PORT`; senha opcional `AUTOCORTES_PASSWORD`
  (HTTP Basic, bloqueio após falhas), obrigatória quando o host não é local. Os IPs do PC entram nos hosts aceitos.
  Iniciadores e Docker Compose passam a aceitar essas variáveis. Ver "Acesso por IP" no README.
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
