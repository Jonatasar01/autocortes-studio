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

A interface fica em `http://127.0.0.1:8000/`, com player, linha do tempo, propriedades
do corte e biblioteca de projetos. O editor clássico foi removido; `/classic` redireciona
para o Studio.

- **Modo manual:** importa e valida o vídeo sem executar transcrição, seleção ou IA.
  Posicione o vídeo e use **Marcar I**, **Marcar O** e **Criar corte** para escolher
  exatamente o trecho. Depois ajuste os tempos, divida e exporte normalmente.
- **Modo automático:** mantém o fluxo existente de transcrição e sugestão de cortes.
  Os trechos podem ser revisados manualmente antes da exportação.
- **Excluir projeto:** pelo ícone de lixeira em *Projetos recentes* ou no cartão de mídia.
  Apaga o vídeo importado, os cortes e as exportações guardadas no projeto (cópias na pasta
  de saída ficam). Se algum arquivo estiver em uso, nada é apagado e o app avisa.
- **Sem interface por enquanto:** narração por IA, configurações de curadoria/IA, FilmCraft,
  piloto automático e reconhecimento de música continuam disponíveis pela API
  (`/api/...`), mas não têm tela no Studio.
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

Projetos ficam em `data/` e reaparecem em **Projetos recentes** após reiniciar.

## Modo autônomo (v2.2)

O app pode trabalhar sozinho, do vídeo bruto até os arquivos prontos para revisão. Abra **⚙ Configurações** e defina:

1. **Declarações padrão de direitos.** Elas preenchem o formulário e valem para a pasta de entrada. Declare apenas o que
   for verdade para todos os vídeos que você costuma importar.
2. **Piloto automático.** Depois da análise, ele escolhe os melhores cortes (até N, com nota mínima) e os exporta nos
   formatos marcados, com legendas, volume normalizado e, se quiser, pausas longas encurtadas.
   - Cortes temporais (sem análise de conteúdo) só entram se você permitir.
   - **Com risco alto de direitos, o piloto pausa.** Ele nunca confirma riscos sozinho: revise a tabela e clique em
     **Continuar piloto automático**.
3. **Narração automática (opcional).** Gera roteiro e voz com ChatGPT para cada corte escolhido. Exige `OPENAI_API_KEY`
   e um consentimento permanente marcado nas Configurações, que fica registrado com data. Se a OpenAI falhar, o corte é
   exportado sem narração (opcional), e falhas temporárias são tentadas de novo automaticamente.
4. **Pasta de entrada (`entrada/`).** Coloque vídeos ali e eles são importados sozinhos quando a cópia termina. O original
   vai para `entrada/processados`. Arquivos inválidos vão para `entrada/falhas`, junto com um `.erro.txt` que explica o
   motivo. Vídeos repetidos não são reprocessados.
5. **Pasta de saída (`saida/`).** Cada corte validado é copiado para `saida/<vídeo>/`, junto com o SRT e um
   **pacote de publicação** (`… - publicação.txt`). O pacote traz título e descrição sugeridos a partir do próprio
   conteúdo, hashtags e a lista do que resolver antes de publicar: pendências de direitos e aviso de voz por IA.

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
- **Curadoria com IA (opcional, paga, com consentimento):** em **⚙ Configurações → Curadoria com IA**, escolha OpenAI
  ou Anthropic e autorize o envio do **texto** das transcrições. A IA escolhe os trechos e dá notas de gancho,
  coerência, valor e tendência, com comentários visíveis em "Por que este trecho?". Títulos e ganchos que não constam
  no trecho são descartados. Se a IA falhar, a seleção heurística é mantida.

```powershell
$env:ANTHROPIC_API_KEY="sua-chave"     # para usar a Anthropic (Claude)
$env:ANTHROPIC_MODEL="claude-sonnet-5-5"  # opcional
# OpenAI usa a mesma OPENAI_API_KEY da narração; modelo opcional em CURATION_OPENAI_MODEL
```

## IA local (opcional)

```bash
pip install -r requirements-ai.txt
```

- **Transcrição:** `faster-whisper` local. Variáveis: `WHISPER_MODEL` (`small` padrão; `base`/`tiny` para pouca memória),
  `WHISPER_DEVICE` (`auto`, `cpu`, `cuda`), `WHISPER_LANGUAGE` (`pt` padrão; `auto` para detectar). O primeiro uso baixa os pesos.
- **Sem transcrição** (não instalada, vídeo sem áudio, falha ou fala insuficiente), os cortes são **temporais** e
  aparecem identificados como tal, sem nota — não houve análise do conteúdo.
- **Ollama:** instale, rode `ollama pull llama3.2` e defina `OLLAMA_ENABLED=1` (opcional `OLLAMA_MODEL`). O app só
  conversa com `127.0.0.1:11434`; títulos sugeridos que não constam no trecho são descartados.

## Narração com ChatGPT (opcional, paga, com consentimento)

Gera um roteiro de narração para cada corte — estilos **recap** (resumo narrado, como canais de "recap" de Shorts),
**comentário** (opinião/análise em primeira pessoa) ou **documentário** — e grava a narração com voz da OpenAI,
abaixando automaticamente o áudio original enquanto o narrador fala.

```powershell
$env:OPENAI_API_KEY="sua-chave"        # obrigatório (cobrança na sua conta OpenAI)
$env:OPENAI_MODEL="gpt-6-luna"         # opcional: modelo do roteiro
$env:OPENAI_TTS_MODEL="gpt-4o-mini-tts"# opcional: modelo de voz
$env:OPENAI_VOICE="cedar"              # opcional: cedar, marin, alloy, ash, coral, echo, nova, onyx, sage...
```

No corte: **Narração com ChatGPT** → escolha o estilo, informe o contexto (ex.: nomes de personagens), marque se quer
enviar quadros do vídeo (a IA "vê" as cenas), autorize o envio e clique em **Gerar roteiro**. Revise e edite os blocos,
marque **Narração com voz IA** na exportação, escolha voz, volume do áudio original e origem das legendas.

- Só é enviado à OpenAI: a transcrição do trecho, o contexto digitado, os quadros (se marcado, até 8 em 512 px) e,
  na exportação, o texto do roteiro. Nunca o vídeo inteiro.
- O áudio gerado fica em cache no projeto: reexportar o mesmo roteiro não gera nova cobrança.
- **A voz é sintética. As políticas da OpenAI exigem informar ao público que ela é gerada por IA.**
- Narração não torna material de terceiros (ex.: episódios de séries/desenhos) licenciado: a revisão de direitos
  continua apontando o risco, e reivindicações de Content ID continuam possíveis. O estilo "comentário" acrescenta mais
  valor original do que apenas descrever a cena.

## Reconhecimento de música (opcional, com consentimento)

Requer conta própria no ACRCloud (pode ter custo):

```powershell
pip install -r requirements-music.txt
$env:ACRCLOUD_HOST="SEU_HOST"; $env:ACRCLOUD_ACCESS_KEY="SUA_CHAVE"; $env:ACRCLOUD_ACCESS_SECRET="SEU_SEGREDO"
```

Marque a opção no formulário. São enviadas até 3 amostras de 12 s (`ACRCLOUD_MAX_SAMPLES`). Correspondência é indício,
não prova; **ausência de correspondência não significa música livre**. Não é Content ID.

## Processamento em lote por CSV (`main.py`)

Para quando você já sabe onde cortar: o sistema não escolhe trechos, só executa a renderização
pesada de cada linha marcada.

```bash
python main.py cortes.csv
python main.py cortes.csv --saida output --palavras 4 --fonte "Montserrat ExtraBold"
```

O CSV aceita `,` ou `;` como separador (veja `cortes.exemplo.csv`):

| coluna | exemplo | observação |
|---|---|---|
| `video_origem` | `entrada/podcast.mp4` | caminho relativo à pasta do CSV ou absoluto |
| `tempo_inicio`, `tempo_fim` | `00:15:30`, `15:30`, `930` ou `00:15:30,5` | |
| `layout` | `single_speaker`, `split_screen`, `pip` | |
| `nome` (opcional) | `Melhor momento` | base do nome do arquivo |

Para cada linha, numa pasta temporária removida ao fim da iteração:

1. **Corte bruto** (`app/batch/slicer.py`): `ffmpeg -ss … -to … -i origem -c copy`, sem recodificar.
   O quadro do início pedido é localizado no temporário pela impressão digital (MD5 de uma
   miniatura), então a sobra até o quadro-chave anterior é descartada com precisão de quadro em
   qualquer versão do FFmpeg.
2. **Legenda** (`app/batch/captions.py`): faster-whisper transcreve só o áudio do trecho; blocos de
   3 a 5 palavras (`--palavras`), em `.ass` amarelo, grosso, com contorno preto, no centro da tela.
3. **Layout 9:16, 1080×1920** (`app/batch/layouts.py`), com a legenda embutida no mesmo passo:
   - `single_speaker`: recorte central; segue o rosto se o OpenCV com `CascadeClassifier` estiver
     disponível (`--rosto auto|sim|nao`).
   - `split_screen`: metade esquerda (pessoa A) em cima, metade direita (pessoa B) embaixo (`vstack`).
   - `pip`: fundo desfocado com o quadro inteiro sobreposto.
4. **Validação** do MP4 (resolução, codec, duração, áudio, decodificação) e gravação em `output/`
   como `001_nome_layout.mp4`, com o `.ass` ao lado.

Linhas inválidas (tempo, layout, vídeo inexistente) são relatadas e puladas sem parar o lote.
O código de saída é 0 só quando todas as linhas deram certo.

Dependências: FFmpeg com libass e as bibliotecas de `requirements-ai.txt` (faster-whisper; OpenCV
para seguir rosto). Sem faster-whisper, os vídeos saem sem legenda e com aviso. O modelo segue
`WHISPER_MODEL` (padrão `small`) e `WHISPER_LANGUAGE` (padrão `pt`).

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
| `AUTOCORTES_JOB_RETRIES` / `AUTOCORTES_RETRY_SECONDS` | 2 / 30 | Novas tentativas para falhas temporárias da OpenAI (espera dobra a cada vez) |

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

A revisão é preventiva e baseada nas suas declarações (e, se autorizado, em amostras de reconhecimento musical).
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
- Consentimentos para roteiro e TTS são registrados no projeto antes do processamento;
  o TTS exige autorização separada na interface. Uma exportação usa o roteiro existente ao entrar na fila.
- A pasta `evidencias/` contém resultados dos testes e capturas. Nenhuma chave, modelo baixado ou vídeo do YouTube
  é distribuído no pacote.

Os nomes `gpt-6-luna` e `gpt-4o-mini-tts` foram conferidos na documentação oficial, mas não houve
chamada autenticada neste ambiente. Os snapshots do mini TTS têm retirada anunciada para 06/01/2027;
revise a integração antes dessa data. A migração para Realtime exige adaptação do protocolo,
não apenas trocar `OPENAI_TTS_MODEL`. Fontes em `ENTREGA_ETAPA1.md`.
