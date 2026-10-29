# AutoCortes Studio Local 2.4.0

Nova central de automação, importação YouTube/Shorts e montagem FilmCraft pela fila. Consulte [o guia da versão](AUTOMACAO-v2.4.0.md). Para instalar os recursos locais de IA e links, use `iniciar_automacao_windows.bat` ou `bash iniciar_automacao_linux_mac.sh`.

# AutoCortes Studio Local

**Edição experimental FilmCraft:** veja [FILMCRAFT-PROTOTIPO.md](FILMCRAFT-PROTOTIPO.md) para o
adaptador CLI validado no Windows, evidências e limites. A interface e a exportação principal permanecem as da v2.3.1.

Aplicação **local** para transformar vídeos longos próprios ou licenciados em cortes MP4 revisáveis, com legendas,
formatos para redes sociais e uma revisão preventiva de direitos. Roda em `http://127.0.0.1:8000`, sem enviar o vídeo
para a internet.

> Leia `AUDITORIA.md` para o relatório completo da auditoria (falhas encontradas, correções e o que foi ou não verificado)
> e `CHANGELOG.md` para as mudanças desta versão.

## Nova interface Studio — edição manual e automática

A interface fica em `http://127.0.0.1:8000/` e abre na **página inicial**:

- **Criar cortes:** botão principal e área de arrastar e soltar. O novo projeto tem 2 passos:
  1. **Vídeo:** escolha ou solte o arquivo. Antes de enviar, o app mostra tamanho, duração e
     resolução e avisa sobre problemas (formato não aceito, acima do limite de tamanho ou duração,
     resolução baixa, vídeo que parece já estar em outro projeto).
  2. **Modo:** *Automático* (sugere os cortes), *Manual* (abre direto no editor) ou *Avançado*
     (automático com duração alvo e todas as declarações de direitos à mostra). O modo escolhido
     fica salvo para o próximo projeto. A importação por link só aparece quando o `yt-dlp` está instalado.
- **Em andamento:** análises e exportações de todos os projetos, com etapa, progresso, tempo e Cancelar.
- **Projetos recentes / Meus projetos:** miniatura, estado e cortes de cada projeto; projetos parados
  mostram **Analisar de novo**.
- **Últimos cortes exportados:** abrir o projeto ou baixar o MP4.
- **Este computador:** FFmpeg, legendas, reconhecimento de fala (GPU ou CPU), seguir rosto e espaço
  livre. O que faltar vem com a instrução de como resolver.

- **Exportar vários cortes:** na aba Cortes, marque os cortes (ou "Todos") e use **Exportar selecionados**. Escolha
  os destinos (Shorts, Reels, TikTok, Horizontal, Original); o app mostra quantos arquivos serão gerados, o tamanho
  estimado e avisa cortes longos demais para cada rede. O andamento aparece como "Lote: 4 de 12 prontos".
  **Abrir pasta** mostra os arquivos no Explorador.
- **Vídeo repetido:** ao escolher um vídeo que já está num projeto, o app avisa antes de enviar e oferece abrir o
  projeto existente.
- **Importar do computador (lives grandes):** no Novo projeto, escolha "Do computador" e cole o caminho do vídeo
  (no Explorador: botão direito → Copiar como caminho). No mesmo disco da pasta do AutoCortes o vídeo entra na hora,
  sem cópia e sem ocupar espaço extra; em outro disco é copiado localmente.
- **Feedback:** marque cortes como 👍 Bom ou 👎 Rejeitar. Isso não muda a exportação: serve para medir e melhorar a
  seleção automática (`python scripts/avaliar_selecao.py <projeto> <projeto> --feedback`).
- **Exportação rápida pela placa:** com NVIDIA, a exportação usa a placa (NVENC), cerca de 2× mais rápida e com a
  mesma qualidade (arquivos ~25% maiores). Configurações → "Exportação rápida pela placa de vídeo".

Barra lateral: Início · Novo projeto · Meus projetos · Editor Studio · Processamentos · Automação ·
Configurações. As abas do editor (Propriedades, Legendas, Efeitos, Áudio) ficam só no editor.
**Configurações** guarda o modo padrão, a duração alvo, o estilo da legenda e a legenda precisa,
e mostra as pastas de entrada e saída. A **Revisão de direitos** fica no painel de mídia do projeto.
Erros comuns trazem a solução junto (ex.: arquivo grande demais → aumentar `MAX_UPLOAD_GB`).

- **Modo manual:** importa e valida o vídeo sem executar transcrição, seleção ou IA.
  Posicione o vídeo e use **Marcar I**, **Marcar O** e **Criar corte** para escolher
  exatamente o trecho. Depois ajuste os tempos, divida e exporte normalmente.
- **Modo automático:** mantém o fluxo existente de transcrição e sugestão de cortes.
  Os trechos podem ser revisados manualmente antes da exportação.
- **Excluir projeto:** pelo ícone de lixeira em *Projetos recentes* ou no cartão de mídia.
  Apaga o vídeo importado, os cortes e as exportações guardadas no projeto (cópias na pasta
  de saída ficam). Se algum arquivo estiver em uso, nada é apagado e o app avisa.
- **Sem interface por enquanto:** FilmCraft e piloto automático continuam disponíveis pela API
  (`/api/...`), mas não têm tela no Studio. Narração por IA e reconhecimento de música
  foram retirados desta branch.
- **Limite atual:** a faixa de vídeo mostra miniaturas reais; não há waveform do áudio
  nem montagem multifaixa.
- **Privacidade:** o servidor continua projetado para `127.0.0.1`. Não exponha
  diretamente a aplicação na internet sem autenticação, revisão de segurança
  e infraestrutura adequada para vídeo.

As edições e cortes são persistidos no projeto local. O player mostra o vídeo
original; os efeitos de exportação são aplicados pelo FFmpeg, não necessariamente
pré-visualizados em tempo real.

---

## Requisitos

- Python **3.12** (aceita 3.10 a 3.12; versões mais novas, como 3.15, ainda não instalam as dependências). O `iniciar_windows.bat` já procura o 3.12 (`py -3.12`); instale com `py install 3.12` se faltar.
- **FFmpeg** com `ffmpeg` e `ffprobe` no PATH (com libass para legendas e libx264). O diagnóstico no topo da interface
  mostra o que foi encontrado.

## Iniciar

**Novo: uv e Docker Compose:** veja [INFRAESTRUTURA.md](INFRAESTRUTURA.md) para instalação com
dependências travadas, execução em contêiner e persistência de projetos. A v2.3.1 também corrige
a detecção de duplicatas e a contabilização de resultados do piloto automático.

**Windows:** extraia o ZIP e execute `iniciar_windows.bat`. O navegador abre em http://127.0.0.1:8000.
Ele instala também a transcrição e a detecção de rostos (`requirements-ai.txt`) e, se houver placa NVIDIA
(`nvidia-smi`), as bibliotecas de CUDA (`requirements-gpu.txt`, cerca de 2 GB). `AUTOCORTES_MINIMAL=1` instala só o
básico. Planos de instalador: [ESTUDO_INSTALADOR_WINDOWS.md](ESTUDO_INSTALADOR_WINDOWS.md).

**Linux/macOS:** `./iniciar_linux_mac.sh`

**Manual:**

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Uso

1. Escolha ou arraste o vídeo, defina a duração e a quantidade de cortes e responda às perguntas de direitos.
2. Acompanhe as etapas (validação, música, transcrição, cenas, seleção, IA). É possível cancelar e reprocessar.
3. Revise a tabela de direitos. Com risco relevante, marque a confirmação para liberar a exportação.
4. Em cada corte: veja os motivos da seleção, ajuste início/fim/título, escolha formato (9:16, 16:9, 1:1 ou original),
   enquadramento (barras ou fundo desfocado, que não cortam a imagem, ou seguir rosto, que recorta as laterais), legendas (clássica ou dinâmica), gancho e normalização de volume.
5. Exporte. O MP4 só aparece depois de validado (duração, resolução, codec, áudio, sincronia e decodificação).
   Baixe o MP4 e o SRT.

Projetos ficam em `data/` e reaparecem na página inicial após reiniciar.

### Quantidade automática de cortes

No modo **Automático** não há campo de quantidade (`desired_count=0`): entram **todos os trechos válidos para
clipe**, ou seja, com nota de pelo menos 80, do melhor para o pior. Não há limite por hora (só um teto técnico
de 500). Numa live de 8,5 h isso deu 144 cortes (71 min somados), entre eles 16 dos 19 melhores momentos
marcados na leitura completa. A API ainda aceita `desired_count` de 1 a 15 para quem quiser um número fixo.
Nesse modo:

- cada corte acompanha o assunto, de 15 s até 2x a duração alvo, e fica a pelo menos 30 s do próximo
  (o mesmo assunto não vira três cortes vizinhos);
- a nota soma, além de clareza e começo/fim limpos, o **interesse** do trecho: história, notícia ou assunto
  pessoal, emoção, dinheiro e valores, reação e humor, troca de perguntas e pessoas citadas pelo nome;
- trechos dominados por instruções de partida ("smoke no meio, drone no mercado") perdem pontos;
- repetições em laço da transcrição ("bora, bora, bora…" dezenas de vezes, típicas do Whisper em música e
  gritaria) são descartadas, e a pergunta no início vale menos (sozinha, não faz um bom corte);
- com curadoria por IA, entram os trechos com nota final de pelo menos 70; no piloto automático,
  `max_clips: 0` exporta todos os cortes aprovados pela nota mínima.

Numa live de 8,5 h, a seleção encontrou cerca de 14 dos 19 melhores momentos marcados na leitura completa
da transcrição; a anterior, cerca de 11. Humor de diálogo (piadas que dependem do tom) continua difícil para
a heurística; a curadoria por IA (Configurações) avalia o conteúdo de fato.

## Modo autônomo (v2.2)

O app pode trabalhar sozinho, do vídeo bruto até os arquivos prontos para revisão. Abra **⚙ Configurações** e defina:

1. **Declarações padrão de direitos.** Elas preenchem o formulário e valem para a pasta de entrada. Declare apenas o que
   for verdade para todos os vídeos que você costuma importar.
2. **Piloto automático.** Depois da análise, ele escolhe os melhores cortes (até N, com nota mínima) e os exporta nos
   formatos marcados, com legendas, volume normalizado e, se quiser, pausas longas encurtadas.
   - Cortes temporais (sem análise de conteúdo) só entram se você permitir.
   - **Com risco alto de direitos, o piloto pausa.** Ele nunca confirma riscos sozinho: revise a tabela e clique em
     **Continuar piloto automático**.
3. **Pasta de entrada (`entrada/`).** Coloque vídeos ali e eles são importados sozinhos quando a cópia termina. O original
   vai para `entrada/processados`. Arquivos inválidos vão para `entrada/falhas`, junto com um `.erro.txt` que explica o
   motivo. Vídeos repetidos não são reprocessados.
4. **Pasta de saída (`saida/`).** Cada corte validado é copiado para `saida/<vídeo>/`, junto com o SRT e um
   **pacote de publicação** (`… - publicação.txt`). O pacote traz título e descrição sugeridos a partir do próprio
   conteúdo, e pendências de direitos a revisar antes de publicar.

Também há **Exportar todos** (todos os cortes em vários formatos de uma vez) e **Rodar piloto automático** em projetos
já analisados. As pastas mudam só por variável de ambiente (`AUTOCORTES_WATCH_DIR`, `AUTOCORTES_OUTPUT_DIR`); a
interface não grava em caminhos arbitrários.

Para deixar o computador processando sozinho: ative a pasta de entrada e o piloto automático, deixe o app aberto e vá
soltando vídeos em `entrada/`. Os resultados aparecem em `saida/` e em **Projetos recentes**.

## Novidades da 2.3 (fusão com o Clipper)

- **Seguir rosto:** novo enquadramento que recorta para 9:16 (ou 1:1) acompanhando quem fala. Diferente de "Barras" e
  "Fundo desfocado", ele **corta as laterais**. Cenas sem rosto ficam com fundo desfocado. Requer
  `pip install -r requirements-ai.txt` (OpenCV); sem ele, a opção aparece desativada.
- **Legenda dinâmica:** poucas palavras por vez, com a palavra falada destacada (estilo dos vídeos curtos). A legenda
  clássica continua disponível, e o SRT segue em frases.
- **Gancho:** frase curta no topo durante os 3 primeiros segundos.
- **Curadoria:** em **Seleção de cortes e IA**, escolha Ollama/Qwen (local, sem chave) ou Google Gemini (nuvem, exige chave e consentimento para enviar o texto da transcrição). Ambos propõem trechos e avaliam coerência e relevância. Falhas mantêm a seleção heurística. Títulos sem respaldo no trecho são descartados.

A chave do Gemini pode ser colada na própria tela (campo **Chave da API do Gemini**, que aparece ao escolher o Gemini). Ela fica em `data/secrets.json`, fora do git e de `settings.json`, e a API nunca a devolve: a tela mostra só os quatro últimos caracteres. Sem chave salva, vale a variável de ambiente:

```powershell
$env:GEMINI_API_KEY="sua-chave" # alternativa à tela; nunca salve a chave no repositório
```

Não há validação com uma chave Gemini real nesta entrega. O contrato REST foi conferido na [documentação oficial](https://ai.google.dev/api/generate-content) e testado com respostas simuladas. Não são enviados áudio ou vídeo. O Ollama precisa estar aberto no mesmo computador em `127.0.0.1:11434`, com `qwen3:4b` ou a reserva `qwen3:1.7b` instalada.

## IA local (opcional)

```bash
pip install -r requirements-ai.txt
```

- **Transcrição:** `faster-whisper` local. Variáveis: `WHISPER_MODEL` (`small` padrão; `base`/`tiny` para pouca memória),
  `WHISPER_DEVICE` (`auto`, `cpu`, `cuda`), `WHISPER_LANGUAGE` (`pt` padrão; `auto` para detectar). O primeiro uso baixa os pesos.
- **Legenda precisa** (Legendas → "Legenda precisa", ligada por padrão): na prévia e na exportação, o áudio só do
  corte é retranscrito com `WHISPER_REFINE_MODEL` (`large-v3-turbo` padrão; `off` desliga) na GPU, e essas frases
  substituem as da análise no projeto. O `small` da análise errava fala rápida, gíria e nomes: num corte de 95 s,
  8 trechos ("um curinguinho a júnior ou uma painazinha a júnior" → "um Coringuinha Júnior ou uma Tainazinha
  Júnior"). A primeira vez baixa ~1,6 GB e leva ~50 s (carregar o modelo); depois o mesmo corte não é refeito.
  Sem GPU NVIDIA, a legenda usa a transcrição da análise e a exportação avisa.
- **Vídeos longos:** acima de 20 min, o áudio é transcrito em partes de ~20 min cortadas em pausas. A memória
  fica fixa (pico de ~1,5 GB) em vez de crescer com a duração; antes, uma live de 8 h falhava por falta de memória.
- **GPU NVIDIA no Windows:** `pip install -r requirements-gpu.txt` instala o cuBLAS e o cuDNN do CUDA 12 (~1,2 GB)
  que o CTranslate2 procura; sem eles a GPU falha com `cublas64_12.dll` e tudo roda na CPU. Na GPU a transcrição
  usa inferência em lote (`WHISPER_BATCH_SIZE`, padrão 8; `0` desliga), redividida em frases.
- **Detecção de cenas:** decodifica na GPU (NVDEC) quando possível e roda em paralelo com a transcrição;
  `AUTOCORTES_HWACCEL=0` força a CPU. Numa GTX 1650, uma live de 8,5 h passou de mais de 2 h (e sem
  transcrição) para 43 min de análise completa.
- **Sem transcrição** (não instalada, vídeo sem áudio, falha ou fala insuficiente), os cortes são **temporais** e
  aparecem identificados como tal, sem nota — não houve análise do conteúdo.
- **Curadoria 100% local com Ollama / Qwen** (sem chave, sem custo, nada sai do computador):
  1. Instale o Ollama ([ollama.com](https://ollama.com); no Windows, `winget install Ollama.Ollama`) e baixe o
     modelo: `ollama pull qwen3:4b` (~2,5 GB; para pouca memória, `ollama pull qwen3:1.7b`, ~1,4 GB, usado
     sozinho quando o 4B não está instalado).
  2. No editor, **Seleção de cortes e IA → Curadoria → Ollama / Qwen local** (aparece "não instalado" enquanto o
     Ollama estiver fechado ou sem o modelo). Não pede consentimento nem chave e não entra no registro de envios
     para a nuvem (`cloud_consents`).
  3. A análise usa o mesmo motor da curadoria na nuvem: o modelo propõe trechos pelo **número das frases** (nunca
     tempos livres), os índices são conferidos, os tempos vêm da transcrição, títulos sem respaldo no trecho são
     trocados e o juiz dá notas de gancho, coerência, valor e tendência. A transcrição é tratada como dado não
     confiável (instruções dentro da fala são ignoradas).
  - Para caber num contexto de 4K e na CPU, os blocos são de 5 min (30 s de sobreposição) e o juiz avalia 4 trechos
    por vez; uma resposta inválida num bloco é pulada, e se o Ollama cair a seleção heurística fica, sem repetir a
    chamada no refinador. Variáveis: `OLLAMA_MODEL` (`qwen3:4b`), `OLLAMA_FALLBACK_MODEL` (`qwen3:1.7b`),
    `OLLAMA_NUM_CTX` (4096), `OLLAMA_KEEP_ALIVE` (`2m`), `OLLAMA_CURATION_TIMEOUT` (300 s por chamada),
    `AUTOCORTES_LOCAL_CURATION_CHUNK_SECONDS` (300), `AUTOCORTES_LOCAL_CURATION_OVERLAP_SECONDS` (30),
    `AUTOCORTES_LOCAL_CURATION_JUDGE_BATCH` (4). Para garantir que o Ollama não use recursos de nuvem dele:
    `OLLAMA_NO_CLOUD=1` no ambiente do Ollama.
  - **Modo rápido** (padrão): pede só número das frases, título e gancho na proposta e só as notas no juiz (até 24
    trechos). Na mesma janela de 1 h: **5 de 5 aprovados com 6 cortes em 496 s (~8 min por hora de vídeo)**. O
    modo completo (com resumo, hashtags e comentários) fica em "Modo da IA local".
  - Os cortes da IA e da heurística são **intercalados** na lista (cada um na sua ordem), porque as notas têm
    escalas diferentes. **Sem limite de cortes** tira o teto de 4 por hora (a nota mínima continua).
  - **Medido no modo completo** (GTX 1650 4 GB + 16 GB de RAM; o `qwen3:4b` ocupa 3,5 GB e roda 66% na placa e 34% no processador),
    numa janela de 1 h da live de referência com 5 cortes aprovados: heurística achou 3 de 5 (4 cortes, 11 s); o
    Qwen achou 4 de 5 (12 cortes, ~45 s por bloco de 5 min e ~54 s por lote do juiz: **~20 min por hora de
    vídeo**); juntos, como o app faz, **5 de 5** (14 cortes). Numa live de 8 h isso dá cerca de 2,5–3 h a mais na
    análise; sem placa de vídeo, mais. A análise mostra o progresso e pode ser cancelada. Com pouca memória, use
    também `WHISPER_MODEL=base` e o `qwen3:1.7b` (mais rápido, qualidade não medida).
  - O refinador antigo do ranking (`OLLAMA_ENABLED=1`) continua disponível e usa o mesmo modelo; ele só roda
    quando a curadoria escolhida não é o Ollama. O app só conversa com `127.0.0.1:11434`, sem proxy.
  - Teste opcional com o Ollama de verdade: `AUTOCORTES_TEST_REAL_OLLAMA=1 python -m pytest tests/test_local_ai.py`.

## Escopo desta versão enxuta

O fluxo principal é importar → transcrever → selecionar → revisar → exportar. A curadoria oferece Ollama/Qwen local e Gemini com consentimento; a heurística é a reserva automática. Narração sintética, reconhecimento musical externo e lote CSV independente foram retirados desta branch. Os arquivos e exportações já salvos não são apagados. Use a versão anterior se precisar desses recursos.

**Sem limite de cortes** remove o teto final por hora, mantendo critérios de qualidade e de não sobreposição. Não significa que a IA examina toda janela possível: o orçamento de candidatos enviados ao juiz ainda cresce aproximadamente 10 por hora (mínimo 24). A opção fica desligada por padrão e vale para novas análises e “Refazer seleção”. Essa última ação reaproveita a transcrição, mas substitui a lista atual de cortes; salve seus ajustes antes.

## Estilo de edição: motor de direção criativa (`app/direction.py`)

Na aba **Efeitos** do Studio, a seção **Estilo de edição** analisa o corte selecionado e gera um
**plano de edição**: uma lista de ações com tempo, parâmetros e justificativa. Cada ação pode ser
desligada antes de exportar. Nada é aleatório; tudo vem de evidência medida no trecho.

**O que é medido**

| Evidência | Como | Usada para |
|---|---|---|
| Tempo de cada palavra | faster-whisper (já existente) | ritmo (palavras/s), pausas |
| Energia da voz por palavra | RMS em janelas de 20 ms do áudio do trecho | ênfases (≥ 4 dB acima da mediana, ou 1,5 dB em fim de exclamação) |
| Pontuação | `?` e `!` da transcrição | perguntas, exclamações, fim de frase |
| Mudanças de cena | detecção de cenas do projeto | zoom nunca atravessa um corte de cena |

O tipo de conteúdo (humor, entrevista, explicação, narrativa, outro) é uma heurística simples sobre a
transcrição (marcas de riso, perguntas, conectivos) e é mostrado com a evidência e uma confiança
baixa/média. Sem transcrição, o plano informa a limitação e não cria ênfases nem cortes de pausa.

**Estilos** (intensidade de 0 a 100% escala zoom, cortes e destaques)

| Estilo | Zoom | Pausas | Legenda | Enquadramento | Cor e transição |
|---|---|---|---|---|---|
| Impacto dinâmico | até 14%, ~1 a cada 8 s | encurta acima de ~0,45 s | dinâmica + destaques | seguir rosto | leve saturação |
| Equilibrado | até 8% | encurta acima de ~0,75 s | dinâmica | fundo desfocado | — |
| Cinematográfico | só aproximação lenta (≤ 5%) em plano longo | preserva (> 1,4 s) | clássica | fundo desfocado | contraste, vinheta, fade 0,4 s |
| Podcast premium | até 6%, espaçado | encurta acima de ~0,8 s | dinâmica | seguir rosto | — |
| Inteligente | escolhe um dos acima pelo conteúdo e explica o motivo | | | | |

Regras fixas: zoom máximo de 20%, rampas suaves (0,25 s de entrada, 0,35 s de saída), duração mínima
de 0,8 s; pausa depois de pergunta nunca é cortada; no cinematográfico e no podcast, pausas de fim
de frase são preservadas; legendas só realçam palavras que foram ditas.

**Versões:** "Gerar 3 versões" cria as versões dinâmica, equilibrada e cinematográfica do mesmo corte,
com um resumo do que cada uma muda (zooms, destaques, segundos de pausa removidos, duração final). A
versão recomendada vem marcada; a escolhida é usada na exportação. O plano completo fica gravado no
registro da exportação, então o resultado é reproduzível.

**Renderização:** tudo na mesma passada do FFmpeg já existente. Pausas usam os intervalos `keep` do
encurtamento de pausas; depois do layout entram `eq`/`vignette` (cor), `zoompan` (zoom, com os tempos
remapeados após o corte de pausas) e `fade`, antes da legenda; no áudio, `afftdn` (redução de ruído,
opcional), `afade` e `loudnorm`. A análise do trecho fica em cache (`direction/clip_NN.json`) e só é
refeita se os tempos ou a revisão do corte mudarem; um plano feito antes de mudar os tempos é recusado
na exportação.

**API:** `POST /api/projects/{id}/clips/{n}/plans` (`style`, `intensity`, `variants`, `options`),
`GET` no mesmo endereço e `PATCH .../plans/{nome}` (`selected`, `actions: {id: true|false}`); a
exportação aceita `edit_plan`.

**Limitações:** não há identificação de falantes (diarização) nem split-screen no editor (o
split-screen existe no lote por CSV); "seguir rosto" depende do OpenCV e cai para fundo desfocado
quando não acha rostos; efeitos sonoros e música não são adicionados automaticamente; a classificação
de conteúdo é heurística; a análise roda na requisição (alguns segundos para cortes longos), não na fila.
Nenhum ganho de retenção é afirmado: não há dados de desempenho.

## Só o streamer (lives de jogo com webcam)

Em **Efeitos > Enquadramento**, **Só o streamer (webcam com fundo desfocado)** mostra apenas a janela da
webcam: ela é recortada, encaixada na largura da tela vertical e a própria webcam, desfocada, preenche em cima e
embaixo. A legenda fica logo abaixo da webcam.

A janela é achada sozinha na análise de rostos: a partir do rosto real (o que se mexe; ícones e personagens
do jogo ficam de fora), procura a borda reta, contínua e presente em todos os quadros mais próxima de cada lado,
enquanto o jogo atrás muda. Lado sem borda (janela encostada na margem) vai até a margem. Se não houver janela
(câmera em tela cheia), o modo segue o rosto e avisa. A janela só é procurada nas laterais (esquerda ou direita):
rostos no meio da tela (jogadores, personagens) não contam. Em transmissões que alternam a bancada com campo ou
jogo, a bancada é seguida e, nas outras cenas, a **janela com os streamers no canto** (picture-in-picture) é
recortada e ampliada; cena sem essa janela aparece inteira com fundo desfocado. O plano de edição define o próprio
enquadramento: para usar "Só o streamer", exporte sem plano.

**Área desenhada à mão:** com "Só o streamer" (ou "Webcam + jogo", ou "Por trecho") escolhido, **Desenhar área no
vídeo** abre uma camada sobre o player: arraste um retângulo em volta do streamer e use **Usar esta área** (Esc
cancela). A área fica guardada no corte (contorno tracejado no player) e a exportação mostra só essa parte, com fundo
desfocado, sem analisar rostos (mais rápido). **Usar a automática** apaga a área. API:
`PUT /api/projects/{id}/clips/{n}/focus` com `{"rect": [x, y, largura, altura]}` em fração do quadro, ou `null`.

Também pode ser usado em trechos do **Por trecho**:
numa transmissão sem janela de webcam, esses trechos seguem rostos só na cena principal (bancada) e mostram as
outras cenas inteiras com fundo desfocado, enquanto os trechos de "seguir rosto" continuam seguindo todos.

## Perfis de exportação

No painel do corte, **Perfil de exportação** aplica de uma vez formato, enquadramento, legenda (estilo e legenda
precisa), filtro de cor, volume normalizado e gancho. Já vêm quatro por tipo de conteúdo: **Live de jogo**
(9:16, webcam + jogo), **IRL / rua** (9:16, só o streamer), **Podcast / entrevista** (9:16, seguir rosto) e
**YouTube horizontal** (16:9, quadro inteiro). O botão **+** salva as opções atuais como um perfil seu (até 20,
guardados em `data/settings.json`); a lixeira exclui. O perfil fica lembrado por projeto, e mudar uma opção à mão
volta para "Personalizado". Ao criar um projeto, **Tipo de conteúdo** já deixa o perfil escolhido.

## Enquadramento por trecho

Na aba **Efeitos**, em **Enquadramento**, a opção **Por trecho (variar no vídeo)** deixa cada parte do corte
com um enquadramento diferente: **seguir rosto**, **fundo desfocado** ou **quadro inteiro**.

- **Mudar a partir daqui:** pare o vídeo onde quer trocar, escolha o enquadramento e clique; o trecho em que
  o vídeo está é dividido nesse ponto.
- **Variar automaticamente:** alterna seguir rosto e fundo desfocado a cada troca de cena do corte (cenas
  com menos de 2,5 s se juntam à seguinte); sem trocas de cena, alterna a cada ~6 s.
- Cada trecho da lista pode ter o enquadramento trocado ou ser removido (o vizinho ocupa o espaço).
- **Início e fim editáveis:** digite o tempo (ex.: `52.5` ou `0:52.5`) e tecle Enter; o limite muda nos dois
  trechos vizinhos (cada trecho fica com pelo menos 0,5 s; valor fora disso é ajustado com aviso). O início do
  primeiro e o fim do último são os do corte. Clicar num campo leva o vídeo até aquele ponto; Esc desfaz.
- Sem trechos marcados, a exportação "por trecho" usa a variação automática.

Na exportação, o FFmpeg monta uma versão do vídeo para cada enquadramento usado e liga cada uma no tempo
do seu trecho, numa única codificação. Seguir rosto sem rosto detectado vira fundo desfocado, como no modo
normal. A legenda fica abaixo da faixa do vídeo nos trechos com desfoque ou barras; nos de seguir rosto (tela
cheia), a mesma altura fica sobre a imagem. API: `PUT /api/projects/{id}/clips/{n}/framing` com
`{"segments": [{"start", "end", "framing"}]}` (segundos do corte) e `POST …/framing/auto`; exportar com
`"framing": "mix"`.

## Segurança visual: legenda dentro do quadro e rosto inteiro no recorte

**Legendas (`app/textfit.py`, `app/subtitles.py`).** As quebras de linha são decididas pela largura real
em pixels, não pelo número de palavras. A largura vem das métricas da própria fonte que o libass usa
(leitor TrueType sem dependências: tabelas `cmap`, `hmtx`, `OS/2`): Arial Bold no Windows, Liberation
Sans ou DejaVu Sans no Linux, ou `AUTOCORTES_SUBTITLE_FONT`. Para cada grupo de palavras:

1. mede no pior caso (qualquer palavra destacada a 108%, termos enfatizados a 112%, contorno e sombra);
2. usa uma linha se couber em 95% da largura útil; senão, a divisão em duas linhas mais equilibrada;
3. só se nada couber reduz o tamanho daquele evento (até 72% e, em último caso, o tamanho exato);
4. fixa as quebras com `\N` e `\q2`, então a linha não "pula" quando o destaque anda.

Vale para a legenda dinâmica, a clássica, o gancho e as legendas do lote por CSV. O cabeçalho passou de
`WrapStyle: 2` (sem quebra) para `0` como rede de segurança. A previsão foi conferida contra o libass:
fica entre 0 e 4% acima da largura renderizada.

**Reenquadramento (`app/reframe.py`).** O detector já devolvia a caixa do rosto, mas o planejador só
usava o centro. Agora:

- cada rosto vira um intervalo de centros de recorte que o mantêm inteiro, com margem de 20% da
  largura do rosto (reduzida para 10% e 0% em close-ups, nunca cortando o próprio rosto);
- se duas pessoas cabem juntas, o recorte mantém as duas; senão, quem fala;
- a suavização continua, mas a segurança tem precedência: a câmera é levada para dentro do intervalo;
- trechos de pelo menos 0,6 s em que nenhum recorte preserva o rosto usam quadro inteiro com fundo
  desfocado só naquele intervalo; o resto da cena continua seguindo o rosto;
- a simplificação do caminho da câmera é verificada amostra a amostra (antes, o corte para 40 pontos
  podia tirar o rosto do quadro).

**Verificação na exportação.** Cada exportação grava `quality.subtitles` (`max_width_ratio`, linhas,
eventos reduzidos, fonte medida) e `quality.reframe` (`face_visibility_min`, `face_visibility_p05`,
amostras abaixo de 90%, segundos em fundo desfocado) e avisa se a legenda passar da área útil ou se um
rosto ficar parcialmente fora.

**Resultado no corte real (41:20–41:50 do episódio):** legenda mais larga em 92% da área útil (antes,
"PERIGOS DOS ACAMPAMENTOS" passava das duas bordas); visibilidade do rosto no percentil 5 = 100%, com
um único close em que o rosto é mais largo que o recorte, mostrado em quadro inteiro por 1 s.

**Quem está falando.** O detector é o YuNet (`cv2.FaceDetectorYN`, modelo de 230 KB em `app/data`,
licença MIT); o Haar só entra se o OpenCV instalado não tiver o YuNet. O Haar marcava papel de parede,
mosaicos e roupas como rostos (até 8 por quadro numa cena com 3 pessoas) e o recorte ia parar no
cenário. Agora:

- detecções que aparecem em menos de 3 amostras (0,6 s) são descartadas;
- quem fala é quem tem mais movimento na boca do que na testa (pelos marcos de boca e olhos do YuNet);
  numa textura, ou com a câmera em movimento, tudo se mexe igual e a diferença fica perto de zero;
- rostos muito menores que o maior da cena (figurantes ao fundo) não concorrem;
- o falante só muda depois de 1 s e com vantagem clara, e a troca para alguém longe, ou uma troca de
  câmera que a análise de cenas não viu, vira corte seco em vez de a câmera deslizar pelo cenário.

No corte "Visão, você está cego" (22,7 s), o recorte ficava 0,7 s no papel de parede e 1,5 s no mosaico
em vez do guarda que falava. Agora todos os trechos mostram um rosto, e a abertura sem pessoas sai em
quadro inteiro com fundo desfocado.

**Limitações:** quem fala é estimado só pela imagem; a identificação pelo áudio (diarização) não existe.
Quando duas pessoas mexem a boca ao mesmo tempo, ou quando quem fala está de costas ou fora do
quadro, o sistema pode escolher a pessoa errada. Não há filtro de Kalman nem aceleração por GPU.

## Linha do tempo do editor

- **Régua:** marcas em h:mm:ss a partir de 1 h (antes uma live de 8 h mostrava "495:00"), com intervalos de
  1 s a 2 h conforme o zoom e marcas menores entre os rótulos. Os tempos da tela toda (cartões, relógio,
  campos de início/fim) usam o mesmo formato.
- **Zoom:** de "vídeo inteiro" até ~60 px por segundo, com quantos níveis forem precisos (17 numa live de 8,5 h).
  **Ctrl + roda do mouse** amplia mantendo parado o ponto sob o cursor; **dois cliques num corte** ampliam até
  ele.
- **Vídeo:** tira de miniaturas gerada pelo servidor (`GET /api/projects/{id}/filmstrip?n=`): o FFmpeg lê um
  quadro por posição e monta um JPEG em cache. O navegador buscava cada quadro no arquivo original e, num vídeo
  de vários GB, a faixa ficava escura.
- **Áudio:** forma de onda real (`GET /api/projects/{id}/waveform`), com picos medidos pelo FFmpeg em segundo
  plano na primeira abertura (~3 min numa live de 8,5 h, depois em cache) e desenhada só na parte visível, em
  qualquer zoom.
- **Cortes:** cor pela nota (verde = melhores), número do ranking sempre visível e título só quando cabe;
  a dica traz título, tempos, duração e nota.

## Prévia da edição

O botão **Prévia** (barra superior) mostra como o corte vai ficar antes de exportar. É a mesma montagem
da exportação (recorte 9:16, faixas pretas removidas, legenda, gancho, plano de edição com zoom, cor e
pausas), renderizada em meia resolução (540×960 no 9:16) com codificação rápida e aberta num player
vertical. Ao lado aparecem a duração, o plano usado, a largura máxima da legenda, a visibilidade do rosto
e os avisos; "Exportar em alta qualidade" gera o arquivo final com as mesmas opções.

- Fica só no computador: não pede a confirmação de direitos, não entra no histórico de exportações, não
  gera pacote de publicação nem cópia na pasta de saída. Só a prévia mais recente de cada corte é guardada
  (`<projeto>/preview/`). Narração por IA (paga) não entra na prévia.
- A análise de rostos fica em cache por corte: a exportação depois da prévia (e as prévias seguintes)
  reaproveitam o resultado. No corte de 30 s do episódio: primeira prévia com "seguir rosto" 36 s,
  seguintes 14 s.
- Depois de exportar, **Assistir** abre o arquivo final no mesmo player.
- API: `POST /api/projects/{id}/clips/{n}/preview` (mesmas opções da exportação) e
  `GET /api/projects/{id}/clips/{n}/preview` (o MP4).

## Configuração

| Variável | Padrão | Função |
|---|---|---|
| `MAX_UPLOAD_GB` | 4 | Tamanho máximo do vídeo |
| `AUTOCORTES_MAX_SOURCE_HOURS` | 6 | Duração máxima do vídeo |
| `AUTOCORTES_MIN_FREE_MB` | 1024 | Espaço livre mínimo preservado no disco |
| `AUTOCORTES_ANALYSIS_WORKERS` / `AUTOCORTES_EXPORT_WORKERS` | 1 / 1 | Tarefas simultâneas |
| `AUTOCORTES_FFMPEG_THREADS` | 0 (auto) | Limite de threads do FFmpeg |
| `AUTOCORTES_LOW_PRIORITY` | 1 | Processos do FFmpeg com prioridade reduzida |
| `AUTOCORTES_X264_PRESET` | veryfast | Velocidade × qualidade da codificação |
| `AUTOCORTES_SCENES` | 1 | 0 desativa a detecção de cenas |
| `AUTOCORTES_DATA` | `./data` | Pasta dos projetos |
| `AUTOCORTES_API_DOCS` | 0 | 1 ativa `/api/docs` |
| `AUTOCORTES_WATCH_DIR` | `./entrada` | Pasta monitorada |
| `AUTOCORTES_OUTPUT_DIR` | `./saida` | Pasta de saída dos cortes validados |
| `AUTOCORTES_WATCH_INTERVAL` | 5 | Segundos entre verificações da pasta de entrada |

## Testes

```bash
pip install -r requirements-dev.txt
pytest                      # ou: python -m unittest discover -s tests -t .
```

Os testes geram vídeos sintéticos com FFmpeg (diversos contêineres, vertical, 4K, sem áudio, VFR, rotacionado,
corrompido) e exercitam exportação, validação, fila, cancelamento, recuperação e segurança.

## Segurança e privacidade

- Use apenas em `127.0.0.1`. **Não** rode com `--host 0.0.0.0`: não há autenticação nem TLS para acesso remoto.
- A interface bloqueia requisições de outros sites (Host, Origin e cabeçalho anti-CSRF) e usa CSP restritiva.
- Os cortes exportados não carregam metadados do original (ex.: localização GPS).
- Segredos só por variáveis de ambiente; não aparecem em respostas, relatórios ou logs.

## Direitos autorais

A revisão é preventiva e baseada nas suas declarações (sem reconhecimento musical externo nesta versão).
Ela separa vídeo original, música, imagens, trechos de terceiros, conteúdo reutilizado e monetização. Nenhuma
categoria é marcada como "livre", e o app não consulta o Content ID. Também não aplica técnicas para enganar sistemas de
detecção (pitch, velocidade, espelhamento). Antes de publicar, confirme licenças e confira as verificações da plataforma.

## Organização

Veja a seção 3.1 de `AUDITORIA.md` para o mapa dos módulos (a v2.2 acrescenta `settings.py`, `autopilot.py`,
`watcher.py` e `publish.py`). `PROMPT_CODEX.md` é o prompt para continuar no Codex. `EVOLUCAO_CODEX_CLAUDE.md` é
apenas histórico do MVP anterior à auditoria.

## Continuação 2.1.1 — entrega da etapa 1

Veja `ENTREGA_ETAPA1.md` para alterações, evidências e pendências. Esta entrega estabiliza
as verificações da v2.1; as funcionalidades das etapas 2–5 ainda não estão implementadas,
exceto a configuração de CI Windows/Linux (execução remota ainda não verificada).

- Windows/Python 3.12: 95 testes e 29 subtestes aprovados, incluindo os seis testes da API.
- Whisper `small`/CPU real: vídeo indicado de 74,51 s, transcrição em 38,44 s; MP4 legendado validado.
- `av>=11,<19` evita incompatibilidade reproduzida entre faster-whisper 1.2.1 e PyAV 19.
- O launcher reutiliza a venv mesmo sem `py` no PATH. `AUTOCORTES_NO_BROWSER=1` suprime a abertura do navegador
  e pausas em erros (útil em diagnóstico automatizado). O servidor permanece em 127.0.0.1.
- A pasta `evidencias/` contém resultados dos testes e capturas. Nenhuma chave, modelo baixado ou vídeo do YouTube
  é distribuído no pacote.

A narração por roteiro/TTS desta etapa foi retirada na versão enxuta; o histórico está em `ENTREGA_ETAPA1.md`.

### Validar a seleção com um segundo vídeo (feedback)

A regra de áudio foi escolhida olhando uma única live de 18 cortes aprovados. Antes de ajustar pesos, avalie um **segundo vídeo independente**, de podcast ou IRL, com fala e transcrição. Não use o mesmo material para calibrar e medir o ganho.

1. Crie o projeto no Studio e aguarde a análise local. Anote o ID de 32 caracteres em `?projeto=...` na URL.
2. Assista aos trechos e marque **👍 Bom** ou **👎 Rejeitado**. “Esconder rejeitados” só filtra a tela; não apaga o feedback. Um segundo clique no mesmo voto o remove. Inclua também cortes manuais bons que o motor deixou passar: aprovar apenas sugestões existentes favorece artificialmente a avaliação.
3. Defina critérios antes da revisão: trecho compreensível sozinho, início/fim completos, fala legível e momento relevante. Registre o número de positivos/negativos e o tipo de conteúdo. O avaliador compara os intervalos registrados no `feedback_log`, não uma opinião sobre viralização.
4. Execute no diretório do repositório que contém `data/<ID>`. Atualmente o script usa essa pasta, **não** `AUTOCORTES_DATA`. Os dois IDs podem ser iguais quando fonte/transcrição e feedback estão no mesmo projeto. Se estiverem em projetos separados, use primeiro o da fonte e depois o da referência.

Exemplo em PowerShell (substitua o ID; escolha uma pasta temporária com espaço):

```powershell
$videoId = 'ID_DE_32_CARACTERES'
$evaluationDir = Join-Path $env:TEMP 'autocortes-avaliacao-segundo-video'
New-Item -ItemType Directory -Force -Path $evaluationDir | Out-Null
$env:AUTOCORTES_SELECTION_ENGINE = 'v1'
# Sem --audio: referência sem a camada de áudio.
.venv\Scripts\python.exe scripts\avaliar_selecao.py $videoId $videoId --feedback --json "$evaluationDir\v1-sem-audio.json"
# Com --audio: gera/reusa somente o cache temporário indicado.
.venv\Scripts\python.exe scripts\avaliar_selecao.py $videoId $videoId --feedback --audio "$evaluationDir\audio-cache" --json "$evaluationDir\v1-com-audio.json"
```

O comando lê a fonte, a transcrição e o feedback; **não substitui a seleção salva**. Não usa OpenAI/Anthropic. A primeira leitura do áudio pode demorar. Sem nenhum 👍, o comando termina com uma mensagem e não produz uma comparação válida. Não rode enquanto houver exportação em andamento.

Compare `recall@18`, `recall@50`, erros medianos de início/fim e `rejeitados_no_top10/18/30`. O critério de correspondência por sobreposição é tolerante: recall não substitui revisão editorial nem comprova que os limites do corte são bons. Registre ambos os JSONs, número de referências e versão/commit. Só discuta alteração de pesos após esta avaliação independente; **nenhum peso é alterado por estes comandos**. A mesma cautela vale para comparar v2.
