# Análise do motor de cortes — auditoria, linha de base e plano

Base auditada: branch `feature/motor-analise`, criada de `feature/fase1-ui` (PR #11), que contém toda a pilha
PR #1 → #5 → #6 → #7 → #8 → #9 → #10. A `master` está 47 commits atrás; PRs #2, #3 e #4 são paralelos (editor
alternativo, acesso pela rede e túnel) e não tocam o motor de análise.
Data da medição: 27/10/2026. Máquina: Windows 11, GTX 1650 4 GB, FFmpeg 9.0.2 (gyan.dev full).

> Observação: o prompt cita `app/static/app.js`; ele não existe mais. A interface é `app/static/studio.js`.

---

## 1. Auditoria (com arquivo e linha)

### 1.1 Como os candidatos são gerados
- A transcrição vira **unidades de fala** (frases): um segmento do Whisper fecha a unidade quando há pausa > 1,2 s,
  a unidade passa de 25 s ou o texto termina em `.!?…` — `app/selection.py:235-262` (regras em `:243` e `:255`).
- **Toda janela contígua de unidades** entre a duração mínima e a máxima vira candidato — laço duplo em
  `app/selection.py:425-436`. No modo automático: 15 s a 2× a duração alvo (`:48-49`, `:417-420`).
  Numa live de 8,5 h isso dá **146.364 janelas** avaliadas.
- Sem transcrição (ou menos de 20 palavras, `:42`), cai para **cortes por tempo** (`temporal_candidates`, `:381-401`).
- Curadoria com IA (opcional): a IA propõe janelas por blocos de 25 min (`app/curation.py:40`, `:254-280`), até 3–20
  por bloco (`:260`), ajustadas à duração por `_fit` (`:217-227`).

### 1.2 Critérios da pontuação (0–100)
`score_window`, `app/selection.py:267-359`:

| Parte | Pontos | Linha | O que mede |
|---|---|---|---|
| Clareza | até 20 | `:277` | fala ÷ duração |
| Confiança da transcrição | até 10 | `:281` | `avg_logprob` do Whisper |
| Início/fim | até 25 | `:291-308` | começa após frase/pausa, não começa com conectivo, termina com pontuação |
| Gancho | até 10 | `:311-321` | `?` e palavras de gancho nos primeiros 6 s |
| Conteúdo | até 15 | `:329` | palavras únicas por segundo |
| Interesse | até 25 | `:330`, `_interest` `:138-180` | listas de palavras: história, notícia, emoção, dinheiro, reação, perguntas, nomes |
| Duração | até 5 | `:344` | proximidade da duração alvo |
| Cena | 3 | `:345` | início a menos de 1 s de uma troca de cena |
| Penalidades | − | `:323-342` | repetição em laço, pouca variedade, pausas longas, fala de jogo |

**Problema confirmado:** a estrutura (clareza + confiança + início/fim + conteúdo) soma até **70 pontos** e quase
sempre sai cheia com a transcrição do `large-v3-turbo`. Na live de 8,5 h: mediana de início/fim = 25/25, confiança
10/10, conteúdo 15/15. Com o piso automático de 80 (`:46`, `:439-443`), basta ~10 pontos de interesse/gancho para
um trecho entrar → **170 cortes** (Just Chatting, 8,5 h) e **195** (IRL, 6,3 h).

### 1.3 Como a curadoria por IA funciona
`app/curation.py:230-393`: (1) a IA propõe trechos por bloco (`:254-280`); (2) cada um recebe também a nota
heurística (`:285-290`); (3) quase-duplicatas saem (`:291-298`); (4) um juiz dá nota a gancho, coerência, valor e
tendência (`:46`, `:300-310`); (5) nota final = 70% IA + 30% heurística (`:43`, `:322`); (6) no automático, para
quando a nota cai abaixo de 70 (`:44`, `:330`).

### 1.4 Quando ocorre fallback
- IA desligada, sem transcrição ou sem consentimento → fica a heurística (`app/pipeline.py:155-162`).
- IA falha (`AppError`) → fica a heurística, com aviso (`app/pipeline.py:177-180`).
- **Quando a IA funciona, a lista da heurística é descartada inteira:** `clips = ai_clips` (`app/pipeline.py:174`).
  Um bom candidato heurístico que a IA não propôs some, sem comparação. É exatamente a regra que o prompt pede
  para mudar.
- Ollama só reordena/rotula a lista heurística quando habilitado (`app/pipeline.py:181-185`,
  `app/selection.py:539+`).

### 1.5 Como repetidos são eliminados
Na seleção gulosa (do melhor para o pior), um candidato é descartado se (`app/selection.py:460-466`):
- sobrepõe mais de 20% de um já escolhido;
- (automático) começa a menos de 30 s de outro escolhido (`AUTO_GAP`, `:50`);
- tem mais de 50% das palavras de conteúdo em comum com um escolhido (Jaccard, `_similar`, `:377`).
A curadoria repete as mesmas regras (`app/curation.py:346-352`). Não há comparação de **assunto** (só de palavras),
nem de evento visual.

### 1.6 Como entrada e saída são calculadas
- Limites = início da primeira unidade e fim da última, com margem de até 0,25 s antes e 0,4 s depois, sem invadir a
  fala vizinha (`app/selection.py:447-455`).
- Não usa VAD, não usa tempo por palavra, não usa cena para ajustar o limite (só como bônus de 3 pontos), não
  procura contexto anterior (pergunta antes da resposta) nem reação posterior.
- Na exportação, o plano de edição pode remover pausas dentro do trecho (`keep_intervals`,
  `app/direction.py:365+`), mas não muda os limites do corte.

### 1.7 Informações visuais usadas na seleção
- **Só as trocas de cena** (FFmpeg `select='gt(scene,0.32)'` a 4 quadros/s, `app/media.py:378-416`), usadas como
  +3 pontos (`app/selection.py:345`).
- Rostos (YuNet/Haar, `app/reframe.py:95-133`), janela da webcam (`:640-684`), movimento por rosto (`:242-262`)
  e cortes por histograma (`:182-191`) existem, mas **só na exportação**, para enquadrar — não informam a seleção.

### 1.8 Onde está o custo (medido, registros de `data/autocortes.db`)

| Etapa | Mediana | Máximo | Observação |
|---|---|---|---|
| Transcrição (análise) | 7 min | 51,5 min (live 8,5 h) | GPU, `large-v3-turbo`, em lotes |
| Cenas (análise) | 2,5 min | 3 min | em paralelo com a transcrição |
| Seleção heurística | 87 s | — | 146.364 janelas, live 8,5 h (medido agora) |
| Exportação: renderizar | 46 s | 279 s | x264 na CPU |
| Exportação: faixas pretas | 14 s | 57 s | refeita a cada exportação |
| Exportação: legenda precisa | 25 s | 166 s | uma vez por corte |
| Exportação: rostos | 15,5 s | 52 s | já em cache |

### 1.9 Análises refeitas sem necessidade
1. **Faixas pretas** recalculadas em toda exportação, com amostras do vídeo inteiro (`app/pipeline.py:268`,
   `app/media.py:452+`). Sem cache.
2. **Modelo Whisper carregado duas vezes na GPU:** a análise guarda o modelo em `_cache`
   (`app/transcription.py:19`, `:72-77`) e o refinamento em `_refine_cache` (`:82`, `:98-106`). Com os dois em
   `large-v3-turbo`, o mesmo modelo fica duas vezes na VRAM (o servidor ocupava ~2 GB de 4 GB).
3. **Energia do áudio** é calculada por corte no plano de edição (`app/direction.py:102-130`, extrai o áudio do corte
   de novo), enquanto o **vídeo inteiro já tem picos de áudio em cache** para a forma de onda
   (`app/timeline.py:129+`, `cache/waveform.json`) — dado que a seleção não usa.
4. O WAV de 16 kHz da análise é apagado logo após a transcrição (`app/pipeline.py:128`), então nenhuma análise
   de áudio posterior consegue reaproveitá-lo.
5. Mudar a quantidade/critério de cortes antes exigia reanalisar tudo; isso já foi resolvido com "Refazer seleção"
   (`app/pipeline.py:43-49`, `:64-67`), que reaproveita transcrição e cenas.

Caches existentes e chaves: transcrição (`transcript.json`, por projeto, sem versão de modelo na chave); cenas
(`project.json: scenes`); rostos (`direction/track_<hash>.json`, chave inclui versão do algoritmo,
`app/pipeline.py:483-498`); miniaturas, filmstrip, forma de onda e amostras de filtro (`cache/`). Nenhum tem limite
de tamanho, mas hoje somam < 2 MB por projeto (as exportações e prévias são o que ocupa: 85–110 MB).

### 1.10 Recursos dos PRs ainda não integrados à seleção
- **PR #7 (direção criativa):** energia por palavra, pausas, perguntas, risos no texto e tipo de conteúdo
  (`app/direction.py:148-217`, `choose_style` `:219-236`) — usados só para o plano de edição, não para escolher cortes.
- **PR #8 (segurança visual):** visibilidade de rosto no recorte — só na exportação.
- **Fase 1 de UI (PR #11):** feedback do usuário ainda não existe (aceitar/rejeitar cortes).

---

## 2. Linha de base de qualidade

**Referência:** os 18 cortes do projeto `4a8838…` (live Just Chatting de 8,5 h), escolhidos lendo a transcrição e
aprovados por você. **Limitação importante:** é um único vídeo e uma única pessoa aprovou; serve para comparar
versões do motor, não como verdade absoluta. O plano inclui ampliar a referência.

Script reproduzível (somente leitura): `python scripts/avaliar_selecao.py 7c572ac2990d413ba8a509ea296f362e 4a8838bed3274c46bfc0f9e76425cbbe`

| Métrica | Motor atual |
|---|---|
| Cortes sugeridos | 170 |
| Recall@5 / @10 | 0,17 / 0,17 (3 de 18) |
| Recall@18 | 0,28 (5 de 18) |
| Recall@30 / @50 | 0,50 / 0,50 |
| Recall com todos os 170 | 0,78 (14 de 18) |
| Erro mediano de início / fim (cortes recuperados) | 13,3 s / 17,1 s |
| Pares duplicados (mais de 50% das palavras em comum) | 0 |
| Duração mediana | 27,7 s (referência: 66 s) |
| Tempo da seleção | 87 s |

Leitura: o motor **acha** a maioria dos momentos bons em algum lugar da lista (14/18), mas **não os coloca no topo**
(só 5 nos 18 primeiros) e **corta curto** (27 s contra 66 s da referência; erro de 13–17 s nos limites, coerente com
a falta de contexto antes e de desfecho depois).

Testes: 288 aprovados com `python -m unittest discover -s tests -t .`. **`uv` e `pytest` não estão instalados nesta
máquina**; o comando `uv run … pytest` do prompt não pôde ser executado. Para rodar localmente:
`pip install uv && uv sync --locked --all-extras && uv run --no-sync python -m pytest -q --durations=15`.

---

## 3. Plano de implementação

Princípios: tudo aditivo; a heurística atual continua como fonte de candidatos e como fallback; cada etapa medida
com `scripts/avaliar_selecao.py` contra a linha de base; nenhum envio externo sem o consentimento que já existe.

### Fase 2 — Seleção semântica, contexto e fusão (prioridade 1)
Por quê: ataca os dois problemas medidos (ranking e limites) sem custo novo de processamento.
- **Ranking que não satura:** separar `technical_score` (estrutura: clareza, confiança, início/fim) de
  `editorial_score` (interesse, gancho, desfecho, contexto). A nota final passa a ser documentada:
  `score = editorial × fator_técnico` (o técnico vira um *portão*, não mais 70 pontos garantidos).
  Adicionar `confidence`, `evidence` e `warnings` ao corte (campos novos; os antigos continuam).
- **Contexto e desfecho:** antes de fechar o corte, incluir a pergunta que antecede uma resposta, a preparação de
  uma reação, e estender até a reação/conclusão logo depois (por palavras e pausas), dentro dos limites de duração.
- **Fusão heurística + IA:** os candidatos da IA entram na mesma lista, com a origem anotada; um candidato heurístico
  forte não some (`app/pipeline.py:174` muda para fusão).
- **Quantidade automática relativa:** em vez do piso fixo de 80, um corte entra se estiver acima de um patamar
  relativo ao próprio vídeo (por exemplo, os melhores por hora de conteúdo), configurável. Resolve os 170/195.
- Testes: pergunta e resposta, piada com reação tardia, frase interrompida, dois eventos próximos, contexto anterior,
  sem transcrição; regressão das regras de duplicação e limites de duração.
- **Aceite:** Recall@18 e erro de limites melhores que a linha de base no mesmo script; nenhum corte com tempo
  inválido; 288+ testes passando; tempo de seleção ≤ 87 s na live de 8,5 h.

### Fase 3 — Camada de áudio (prioridade 2)
- **Sem dependência nova:** guardar o WAV da análise até o fim da seleção e extrair, com FFmpeg (`astats`/`ebur128`),
  energia por janela; usar o VAD Silero que o faster-whisper **já embute** (`vad_filter=True`,
  `app/transcription.py:123`, `:265`) para fala/silêncio; pausas e ritmo vêm dos tempos por palavra.
- Eventos com início, fim, tipo, intensidade, confiança e origem: picos de energia, silêncio longo, aceleração da fala,
  "possível risada" (energia sem palavras + "kkk/haha" no texto, sempre com confiança baixa), áudio comprometido.
- Regra: áudio alto **sozinho** não faz um corte; só reforça um candidato que já tem conteúdo.
- Reaproveitar as funções de energia de `app/direction.py` em vez de duplicar.
- **Não recomendo agora:** pyannote (diarização) — modelo grande, licença com aceite no Hugging Face e VRAM que a
  GTX 1650 não tem de sobra; librosa — o FFmpeg já cobre o que precisamos.

### Fase 4 — Camada visual (prioridade 3)
- Aproveitar a **mesma passada** da detecção de cenas (o vídeo já é decodificado inteiro a 4 quadros/s na GPU,
  `app/media.py:378-416`) para gravar também intensidade de movimento por janela — sem decodificar de novo.
- Segunda passada só nos candidatos do topo: rostos/webcam com o `app/reframe.py` existente (reação simultânea do
  jogador, troca câmera ↔ jogo).
- Detectores de jogo (placar, vitória/derrota, HUD) **só por jogo e com validação**; fora do escopo inicial.

### Fase 5 — Motor multimodal e processamento em duas passadas
- Candidatos de todas as fontes numa estrutura comum (heurística, áudio, visual, IA, manual), consolidação,
  complemento de contexto, ranking e seleção com diversidade.
- Modo **rápido** (o atual + sinais leves) e **aprofundado** (segunda passada visual/semântica só nos melhores),
  cancelável e com cache versionado.

### Fase 6 — Perfis por nicho
Gaming, Podcast, Humor, Educação, Notícias, Geral e Personalizado: pesos das dimensões editoriais e regras de
contexto (ex.: Humor exige preparação + reação; Podcast exige pergunta + resposta completa). Pesos calibrados com a
referência, não fixos para sempre. Compatível com `target_seconds`/`desired_count` atuais.

### Fase 7 — Interface, feedback e avaliação
Cada corte com evidências, nota editorial e técnica, alertas; ordenar por nota/tempo; filtrar; favoritar e rejeitar;
reclassificar sem retranscrever (já possível com "Refazer seleção"). Feedback (aceito, rejeitado, início/fim
ajustados) gravado no projeto, só local, para ampliar a referência de avaliação.

### Fase 8 — Desempenho e confiabilidade
Cache de faixas pretas; um único modelo Whisper na VRAM (unificar `_cache`/`_refine_cache`); limite e limpeza de
caches; métricas de tempo, memória e acertos de cache registradas por etapa.

---

## 4. Decisões que precisam da sua aprovação

1. **Nota final.** Manter um número de 0 a 100 (calculado pela fórmula documentada acima) ou mostrar só
   classificação editorial + técnica? Recomendo manter o número, porque a interface, o piloto automático e o
   `min_score` da exportação em lote já dependem dele.
2. **Quantidade automática.** Trocar o piso fixo de 80 por um patamar relativo ao vídeo, com teto configurável
   (sugestão inicial: até ~4 cortes por hora de vídeo, ajustável). Isso muda o resultado do modo automático.
3. **Fusão com a IA externa.** Quando a curadoria estiver ligada, juntar as duas listas (recomendado) em vez de
   substituir.
4. **Referência de avaliação.** Hoje há 18 cortes de referência de 1 vídeo. Para medir de verdade, preciso de mais
   marcações humanas (ideal: 2–3 vídeos de tipos diferentes). A Fase 7 cria isso pela interface; até lá, sigo com a
   referência atual e deixo claro o limite.

## 5. Riscos
- **Referência pequena:** ganhos medidos em 1 vídeo podem não se repetir em outros tipos de conteúdo.
- **Mudança de comportamento do automático:** projetos existentes não mudam (os cortes ficam gravados); só novas
  análises e "Refazer seleção" usam o motor novo. O motor antigo fica disponível como fallback/comparação.
- **Custo da Fase 4** na GTX 1650: a segunda passada visual precisa de limite de candidatos e cancelamento.
- **Sinais de áudio enganosos** (música, jogo alto): por isso o áudio só reforça, nunca decide sozinho.

## 6. Reversão
As fases ficam em branches próprias a partir de `feature/motor-analise`. O motor atual continua no código
(`selection.select_clips`); voltar é trocar de branch ou desligar o motor novo por configuração.

---

## 7. Relatório da Fase 2 — seleção semântica, contexto e fusão

Branch: `feature/motor-fase2` (a partir de `feature/motor-analise`).

### O que foi implementado
- **Quantidade automática relativa** (decisão 2): até `AUTOCORTES_AUTO_CLIPS_PER_HOUR` cortes por hora (padrão 4,
  mínimo 3), escolhidos do melhor para o pior (`selection.auto_limit`).
- **Fusão heurística + IA** (decisão 3): `selection.merge_candidates`. O pipeline não descarta mais a lista da
  heurística quando a curadoria funciona (antes: `clips = ai_clips` em `app/pipeline.py`).
- **Campos explicáveis no corte:** `sources`, `evidence`, `warnings`, `confidence`, `editorial_score`,
  `technical_score`. Compatível: os campos antigos (`score`, `score_breakdown`, `reasons`) continuam.
- **Motor v2** (`selection.score_window_v2`, opcional via `AUTOCORTES_SELECTION_ENGINE=v2`), com a fórmula documentada
  no código (nota = editorial × portão técnico):
  - assunto sustentado, com a raridade da palavra no próprio vídeo (a partir de 300 frases);
  - pergunta e resposta no mesmo corte; penalidade para resposta sem a pergunta, fim numa pergunta e corte antes da
    reação; seguir além do desfecho sem voltar ao assunto;
  - a preparação da história ("preciso contar…") conta como gancho;
  - pausas de live toleradas (1 pausa longa a cada 30 s);
  - vizinhos de assuntos diferentes podem virar dois cortes.
- O motor v1 continua inteiro (`score_window_v1`) e é o **padrão**.

### Medições (live Just Chatting de 8,5 h, 18 cortes de referência, mesma máquina)

| Variação | Cortes | R@5 | R@10 | R@18 | R@30 | Erro início/fim | Duração | Seleção |
|---|---|---|---|---|---|---|---|---|
| Linha de base (v1, piso 80) | 170 | 0,17 | 0,17 | 0,28 | 0,50 | 13,3 / 17,1 s | 28 s | 80–87 s |
| **Padrão novo** (v1, até 4/h) | **34** | 0,17 | 0,17 | **0,28** | **0,50** | 11,5 / 17,1 s | 41 s | **75 s** |
| v2 com raridade | 34 | 0,11 | 0,11 | 0,17 | 0,28 | 22,8 / 30,9 s | 70 s | 85 s |
| v2 sem raridade | 34 | 0,11 | 0,17 | 0,28 | 0,33 | 19,2 / 17,8 s | 74 s | — |
| v1 + contexto do v2 | 34 | 0,06 | 0,11 | 0,28 | 0,39 | 20,3 / 15,4 s | 54 s | — |

Reproduzir: `python scripts/avaliar_selecao.py 7c572ac2990d413ba8a509ea296f362e 4a8838bed3274c46bfc0f9e76425cbbe`
(com `AUTOCORTES_SELECTION_ENGINE=v2` para o v2).

### Nova medição (31/10/2026): cortes exportados ou aprovados nas lives do usuário

Referência: os 13 cortes que o usuário exportou ou marcou com 👍 em 5 lives (3,5 h a 11 h), modo automático.

| Motor | Recuperados (de 13) | Duração média | Duplicados |
|---|---|---|---|
| v1 | 4 | 34–46 s | 0 |
| v2 | 6 | 53–59 s | 0 |

Somada à referência de 18 cortes acima, o v1 recupera 13 de 31 e o v2 cerca de 12, com o v1 melhor no topo da
lista (R@30 0,50 × 0,28–0,33). **O v1 continua o padrão.** O aprendizado com o 👍/👎 (`app/preference.py`) passa a
ajustar a nota dos dois motores conforme as avaliações se acumulam.

### Conclusão
- O ganho comprovado é de **quantidade e organização**: 5 vezes menos cortes para revisar, sem perder os bons do
  topo, e a curadoria por IA deixa de apagar a heurística.
- **O ranking não melhorou.** Nenhuma variação do v2 superou o v1 na referência; as diferenças (1–2 cortes em 18)
  estão dentro do ruído de uma referência pequena. Só há sinais de texto, e a nota de "assunto" tende a saturar em
  trechos longos de live (toda conversa de 1 min repete alguma palavra).
- Os testes de contexto (pergunta e resposta, reação tardia, frase interrompida, histórias próximas) passam no v2 com
  transcrições sintéticas, mas isso não virou ganho na live real.

### Limitações
- Referência: 18 cortes de 1 vídeo, escolhidos lendo a transcrição (viés a favor de sinais de texto).
- `editorial_score` e `technical_score` só são preenchidos no v2; no v1 `evidence` sai vazio.

### Próximas etapas (ordenadas)
1. **Sinais que não são texto (Fase 3, áudio):** energia, risadas e picos, aproveitando o cache da forma de onda. É a
   aposta com mais chance de separar momento bom de conversa comum, o que o texto sozinho não conseguiu.
2. **Referência maior:** marcar cortes bons e ruins em mais 2–3 vídeos de tipos diferentes (pela interface, Fase 7).
3. Revisitar o v2 depois de 1 e 2, com o áudio somado ao contexto.

### Reversão
`AUTOCORTES_AUTO_CLIPS_PER_HOUR=30` aproxima o comportamento anterior ("todos os válidos"); voltar à branch
`feature/motor-analise` desfaz a fase inteira. Projetos existentes não mudam (os cortes ficam gravados no projeto).

---

## 8. Relatório da Fase 3 — camada de áudio

Branch: `feature/motor-fase3` (a partir de `feature/motor-fase2`).

### O que foi implementado (`app/audio_events.py`, sem dependências novas)
- **Energia do vídeo inteiro:** FFmpeg mede o nível RMS a cada 0,2 s numa única passada pelo áudio; cache
  `cache/audio_energia_v1.json` com a versão e o carimbo do arquivo (tamanho e data). Na análise, a energia sai do WAV
  que já é extraído para a transcrição (não decodifica o vídeo de novo); em "Refazer seleção", sai do vídeo ou do
  cache.
- **Eventos com evidência** (início, fim, tipo, valor, confiança, origem): pico de volume (9 dB acima da mediana dos
  ±30 s), possível risada/reação (som forte sem palavras transcritas logo depois de fala), risada transcrita ("kkk",
  "haha"), silêncio, áudio estourado e fala acelerada.
- **Reforço na seleção** (vale para v1 e v2): +5 quando um trecho **que já tem conteúdo** termina com reação sonora
  (no fim do trecho ou nos 2 s seguintes); alertas de áudio estourado (−5) e silêncio longo (−3). O fim do corte se
  estende até o fim da risada, sem invadir a fala seguinte.
- Na análise: etapa "Reações no áudio", resumo em `project.json: audio_analysis` (contagem por tipo e tempo), e
  `AUTOCORTES_AUDIO_LAYER=0` desliga. Falha na camada de áudio vira um aviso; a seleção segue só com o texto.
- Desempenho: a verificação de troca de cena por janela virou busca binária (antes percorria as ~900 cenas de uma
  live a cada uma das ~146 mil janelas: 13,7 milhões de comparações no perfil).

### O áudio separa os cortes bons? (medido antes de integrar)

| Medida (mediana / fração com valor) | Aprovados (18) | Topo não aprovado (25) | Aleatórios (300) |
|---|---|---|---|
| Reação sonora no fim | 0,47 s / **28%** | 0,06 s / **8%** | 0,76 s / 45% |
| Picos de volume por minuto | 5,1 | 6,4 | 7,7 |
| Energia acima do normal | 3,2 dB | 3,8 dB | 3,5 dB |

Só a **reação no fim** separa os aprovados dos outros candidatos do topo. Picos e energia média não separam (numa live
de jogo, o barulho alto é do jogo; as histórias boas são mais calmas), por isso não dão pontos. Trechos aleatórios
têm muita "reação" porque o som do jogo sem fala parece reação: daí a regra de que o áudio só reforça quem já tem
conteúdo.

### Medições (live de 8,5 h, 18 cortes de referência)

| | R@5 | R@10 | R@18 | R@30 | R@50 | Erro início/fim |
|---|---|---|---|---|---|---|
| Fase 2 (v1, até 4/h) | 0,17 | 0,17 | 0,28 | 0,50 | 0,50 | 11,5 / 17,1 s |
| **Fase 3 (v1 + áudio)** | 0,17 | 0,17 | **0,33** | 0,50 | **0,56** | 11,7 / 16,5 s |
| v2 + áudio | 0,11 | 0,11 | 0,17 | 0,22 | 0,33 | 22,8 / 24,4 s |

Custo (medido com a máquina ocupada por uma exportação, por isso os tempos absolutos estão altos):
- energia do vídeo inteiro a partir do arquivo original: **86 s** na primeira vez, **0,01 s** pelo cache;
- eventos e índice: **1,7 s**;
- seleção com áudio: **+7 s** sobre a sem áudio na mesma rodada (169 × 162 s, cerca de 4%). Numa segunda rodada a
  carga da máquina mudou no meio (a exportação terminou: 103 s com áudio × 165 s sem), então ela não serve de
  comparação. Falta medir com a máquina parada.

### Conclusão
- Ganho pequeno e no sentido certo: **1 corte aprovado a mais** entre os 18 primeiros e entre os 50, e o fim do corte
  passa a incluir a risada que vem depois da última frase.
- **O ganho é otimista:** a regra (reação no fim) foi escolhida olhando esta mesma referência. Precisa ser confirmada
  em outro vídeo marcado antes de ser considerada comprovada.
- O motor v2 continua pior que o v1, também com áudio.

### Limitações
- "Possível risada" é som forte sem palavras: pega também grito, aplauso e som de jogo. Por isso a confiança é baixa
  (0,4) e o evento só reforça trechos com conteúdo.
- Não há diarização (quem fala) nem sobreposição de falantes; pyannote segue fora pelos motivos da seção 3.

### Próximas etapas
1. Validar a regra num segundo vídeo marcado (gaming ou podcast).
2. Fase 4 (visual): movimento intenso e troca câmera/jogo na mesma passada da detecção de cenas.

### Reversão
`AUTOCORTES_AUDIO_LAYER=0` desliga a camada; voltar à `feature/motor-fase2` desfaz a fase. O cache de energia pode ser
apagado sem efeito colateral (é refeito).

### Custo medido com a máquina parada (Fase 4 de melhorias)
- Energia do vídeo inteiro a partir do arquivo original, sem cache, mais os eventos: **106 s** (antes, com a máquina
  ocupada, 86 s + 1,7 s; a diferença vem do disco: a leitura das 8,5 h do arquivo domina o tempo). Na análise de
  verdade a energia sai do WAV já extraído para a transcrição; em "Refazer seleção", do cache (0,01 s).
- Seleção com e sem áudio, alternadas: 72,8 / 79,8 s com áudio × 83,5 / 71,6 s sem. A diferença (média 76,3 × 77,6 s)
  fica dentro da variação entre rodadas: **a camada de áudio não tem custo mensurável na seleção**.

## 9. Relatório da Fase 4 — camada visual (medida, não integrada)

### O que foi medido
Diferença entre quadros (`scene_score` do FFmpeg) a 4 quadros/s em todas as 8,5 h, a mesma decodificação da
detecção de cenas (NVDEC, `-skip_frame noref`, 192 px): 121 784 quadros em **1268 s** como passada separada
(a máquina rodava testes ao mesmo tempo). Por corte: movimento médio, movimento relativo aos ±60 s, cortes de cena
por minuto, pico no fim, movimento no fim × começo e fração de tela parada (diferença < 0,002).

| Mediana | Aprovados (18) | Topo não aprovado (25) | Aleatórios (300) |
|---|---|---|---|
| Movimento médio (×100) | 0,86 | 2,12 | 4,28 |
| Tela parada | **47%** | **17%** | 11% |
| Pico no fim (× referência local) | 9,9 | 7,5 | 14,3 |
| Cortes de cena por minuto | 0 | 0 | 1,7 |

AUC aprovados × topo não aprovado (0,5 = não separa): tela parada 0,66, movimento médio 0,34 (ou seja, 0,66 no
sentido inverso), pico no fim 0,61; o resto ≈ 0,5.

**Leitura:** o contrário do que a Fase 4 previa. Os cortes bons desta live são **calmos** (conversa na bancada,
câmera parada); movimento intenso é o jogo, que aparece mais nos candidatos não aprovados e nos aleatórios.

### O sinal melhora o ranking?
Reforço de `w × (tela parada − 0,25)` na nota dos 34 candidatos do automático (v1 + áudio):

| Peso | R@5 | R@10 | R@18 | R@30 | R@50 |
|---|---|---|---|---|---|
| 0 (atual) | 0,17 | 0,17 | 0,33 | 0,50 | 0,56 |
| 5 | 0,06 | 0,17 | 0,39 | 0,50 | 0,56 |
| 10 | 0,17 | 0,17 | 0,39 | 0,50 | 0,56 |
| 20 | 0,17 | 0,22 | 0,28 | 0,50 | 0,56 |

Validação cruzada (20 divisões: metade dos aprovados escolhe o peso, a outra metade mede): ganho médio de recall
**−0,032** (mínimo −0,133, máximo 0). O texto e o áudio já colocam as conversas calmas no topo; o visual só
embaralha a ordem.

### Decisão
- **Não integrado à seleção.** Com uma referência de 18 cortes de um só vídeo, o sinal não se sustenta fora da amostra
  e custaria uma passada a mais (ou um arquivo de medidas maior na detecção de cenas) sem ganho.
- O script da medição fica descrito aqui para repetir quando houver mais marcações (👍/👎 da Fase 3 de desempenho,
  `scripts/avaliar_selecao.py --feedback`), de preferência num vídeo de outro tipo (podcast, IRL).
- O visual continua sendo usado onde já provou valor: enquadramento (rostos, janela da webcam, cena principal).
