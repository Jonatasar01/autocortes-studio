# AutoCortes + FilmCraft — protótipo Windows

Base: ZIP AutoCortes Studio Local v2.3.1 fornecido pelo usuário. Entrega de 23/10/2026.

## Escopo entregue

- Recuperação de `project.json.bak` na listagem de projetos. Uma cópia válida é restaurada atomicamente como `project.json`, preservando o backup.
- Adaptador opcional `app/integrations/filmcraft`, separado das rotas FastAPI e do pipeline FFmpeg existente.
- Detecção de binário por `--binary`, variável `FILMCRAFT_CLI`, PATH ou pasta padrão do Windows.
- Versão compatível fixada em **FilmCraft CLI 0.12.0**; inventário de comandos consultado no executável real antes da montagem. Outra versão exige nova validação.
- Contrato `autocortes-edit-v1`, hash completo da fonte, FPS racional e conversão para 254.016.000.000 ticks/s em uma única fronteira, ajustada ao fotograma mais próximo.
- Importação, descoberta de IDs, montagem com áudio, importação de SRT, salvamento nativo `.fcproj`, exportação H.264/AAC, inspeção de trilhas e validação com FFprobe/FFmpeg.
- Logs JSON, limite de tempo, cancelamento pelo adaptador e publicação de `render.mp4` somente após validação. Saídas sempre em uma pasta nova: projetos existentes não são sobrescritos.

O FilmCraft funciona como processo separado por CLI. Não é aberto servidor TCP/MCP nem canal de controle desktop. O adaptador não envia mídia a APIs pagas.

## Executar

Use o ambiente do AutoCortes com FFmpeg/FFprobe no PATH. Baixe a distribuição portátil oficial **0.12.0 para Windows x64**, confira seu SHA-256 e extraia-a. Não é necessário instalar Rust. O executável do FilmCraft não está incluído no ZIP deste projeto.

```powershell
$env:FILMCRAFT_CLI = 'C:\ferramentas\FilmCraft\filmcraft-cli.exe'
uv run --no-sync python -m app.integrations.filmcraft --diagnose
uv run --no-sync python scripts/filmcraft_smoke.py --binary $env:FILMCRAFT_CLI --out .\teste-filmcraft
```

Se o ambiente ainda não existir, execute primeiro `uv sync --locked --all-extras --python 3.12`. A pasta de saída deve ser nova. O script gera oito segundos de mídia sintética local e monta dois trechos de 2,002 s, com legendas de teste e áudio.

Para converter cortes de um projeto AutoCortes já analisado:

```powershell
# Use o mesmo AUTOCORTES_DATA da sua instalação, com o projeto estável/sem tarefas em andamento.
uv run --no-sync python -m app.integrations.filmcraft --project-id ID_DO_PROJETO --clips 1 2 --out .\montagem-filmcraft
```

Também aceita `--manifest caminho.json`. Um manifesto real pode ser visto no diretório da demonstração. Abra `timeline.fcproj` pelo FilmCraft desktop para edição manual. Ao mover a demonstração para outra máquina, regenere-a com o script ou religue a mídia pelo editor: o projeto contém referências a caminhos locais. Não altere o arquivo nativo manualmente.

Se o binário estiver ausente, incompatível ou falhar, a CLI termina com mensagem específica. O AutoCortes continua oferecendo a exportação FFmpeg existente; este protótipo não dispara fallback automático nem troca a rota de produção.

## Evidências reais

Testado com FilmCraft CLI **0.12.0**, Windows x64 e FFmpeg 9.0.2. O SHA-256 do ZIP portátil coincidiu com SHA256SUMS.txt oficial:

`6732c6151e532eaa548d8039740091db813730e7112fdf3ab2837a1cf0ad0c3a`

- Importação real retornou o ID de mídia; `.fcproj` foi salvo e reaberto pela própria CLI.
- Dois cortes em 30000/1001 FPS: **120 fotogramas, 4,004 s de vídeo**. O áudio exportado tem 4,010 s, diferença de 6 ms; ambos começam em zero.
- As trilhas V1/A1 apresentaram os mesmos ticks de início/fim e de origem previstos pelo contrato. A inspeção confirmou dois cues de legenda; um frame renderizado foi conferido visualmente com texto acentuado gravado na imagem.
- Render sem áudio de origem: aprovado.
- Conversão a partir de uma fixture de projeto AutoCortes persistida: aprovada.
- Cancelamento durante exportação FilmCraft real: processo interrompido e nenhum `render.mp4` final publicado. Arquivos de diagnóstico e eventual parcial permanecem na pasta da tentativa para análise.
- A decodificação integral dos renders foi feita pelo FFmpeg com `-xerror`.

O resultado da suíte Python final e os logs ficam em `evidencias/filmcraft/`. Os testes unitários de 23.976/29.97/59.94 FPS verificam a matemática de uma hora; **não equivalem a renderizar vídeos reais de uma hora**. O teste sintético de áudio verifica tempos de streams, não sincronismo labial perceptual.

Suíte completa executada nesta entrega: **164 testes e 35 subtestes aprovados em 160,24 s**, em Windows/Python 3.12.14. Inclui três casos de recuperação de backup e onze casos do adaptador. Os renders FilmCraft descritos acima foram executados adicionalmente, fora dos testes unitários.

## Etapa opcional: remover pausas pelas palavras da transcrição

Desligada por padrão. No painel "Montagem no FilmCraft" marque **Remover pausas longas**, ou envie
`{"remove_pauses": true, "pause_min_seconds": 0.7, "pause_keep_seconds": 0.25}` para
`POST /api/projects/{id}/advanced`.

Como funciona (`FilmCraftBridge.remove_pauses`, fase 2 do `build`):

1. A montagem normal é criada e validada com posições exatas (nada muda aqui).
2. As palavras de cada corte (tempo da fonte, vindas de `word_timestamps` do faster-whisper) são enviadas com
   `transcript.set` em ticks exatos (`ms × 254.016.000`).
3. `transcript.removePauses` apaga, em *ripple*, as pausas maiores que `pause_min_seconds`, mantendo
   `pause_keep_seconds` de silêncio de cada lado (o silêncio mantido nos dois lados deve ser menor que a pausa mínima).
4. `transcript.createCaptions` + `captions.export` recriam o SRT a partir da transcrição **já cortada**. O SRT
   importado dos títulos não é usado nesse modo, pois ficaria fora de sincronia.
5. Como as posições exatas mudam, a validação passa a ser estrutural: sem lacunas nem sobreposições, cada trecho restante
   vem de um corte original e na ordem da fonte, vídeo e áudio idênticos, duração removida igual à relatada pelo
   FilmCraft (±1 quadro) e legendas dentro da duração. O render final é conferido contra a nova duração.

Requisitos e limites:

- Exige transcrição local **por palavra** em todos os cortes; senão a API responde `409 no_words` antes de enfileirar.
- O Whisper costuma **omitir hesitações** ("um", "uh"); esta etapa remove *silêncio*, não vícios de fala
  (`transcript.removeFillers` não é usado).
- O resultado da tarefa inclui `pause_removal` (`removed`, `seconds`, `duration_before`, `duration_after`).
- Testes: `tests/test_filmcraft_pauses.py` (sem FilmCraft nem FFmpeg reais). Em 23/10/2026 o `build` completo também
  foi executado com o `filmcraft-cli` 0.12.0 real, com FFprobe/FFmpeg simulados a partir do `probe` do FilmCraft;
  uma execução com FFmpeg real continua pendente.

## Limites explícitos

- É um protótipo da etapa P1, não a integração completa do roadmap. A interface da imagem, timeline web, botão de abrir FilmCraft e conciliação de edições externas não foram implementados.
- Mantém uma faixa de vídeo e uma de áudio; requer fonte CFR e FPS de saída igual ao da fonte. Fontes VFR e FPS incompatíveis são recusados.
- Preserva resolução original no conversor de projeto. Apenas enquadramento `fit` é aceito; não transporta rastreamento facial, blur, efeitos, pausas encurtadas ou narração IA.
- As legendas deste protótipo são **títulos dos cortes**, para demonstrar SRT e Unicode. Não são transcrição da fala. Transporte da transcrição/SRT editorial é etapa posterior.
- Não há bloqueio entre processos por diretório de dados nesta entrega. Não execute duas instâncias AutoCortes sobre o mesmo AUTOCORTES_DATA. Esse item P0 do roadmap continua pendente.
- Não foram executados Docker, GPU, APIs pagas, editor desktop interativo, comparação perceptual com FFmpeg ou vídeos longos reais.
- O adaptador não é uma API HTTP para execução arbitrária de comandos. Os comandos são internos e fixos; arquivos de manifesto são entradas locais de confiança do operador.

## Fontes e licenças

O adaptador foi escrito neste projeto; não foi copiado código de `artcraft-services`, ArtCraft, PhotoCraft ou LightCraft. O FilmCraft foi usado como dependência externa durante os testes; seus executáveis e marcas não são redistribuídos neste ZIP.

- [Distribuição usada: v0.12.0](https://github.com/storytold/filmcraft/releases/tag/v0.12.0)
- [Licença MIT da versão](https://github.com/storytold/filmcraft/blob/v0.12.0/LICENSE-MIT)
- [Licença Apache-2.0 da versão](https://github.com/storytold/filmcraft/blob/v0.12.0/LICENSE-APACHE)
- [NOTICE e termos separados para marcas](https://github.com/storytold/filmcraft/blob/v0.12.0/NOTICE)
- [Atribuições de ativos](https://github.com/storytold/filmcraft/blob/v0.12.0/ATTRIBUTION.md)
- [Documentação de automação](https://github.com/storytold/filmcraft/blob/v0.12.0/docs/agents.md)

Correção do relatório de entrada: a documentação atual do canal desktop exige `FILMCRAFT_CONTROL_TOKEN`. Este protótipo não utiliza esse canal. A evidência de compatibilidade é a versão/binário registrado em `diagnostic.json`, não uma suposição baseada no branch main.
