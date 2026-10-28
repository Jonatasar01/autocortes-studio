# Prompt para o Codex: verificar as fases 1 a 4 e dar seguimento

> Cole este texto no Codex com o repositório `autocortes-studio` aberto no branch `feature/fase4-melhorias`
> (PR #16, o topo da pilha). Trabalhe em português.

## 1. Missão

Você é um revisor e desenvolvedor sênior: Python 3.12, FastAPI, JavaScript sem framework, FFmpeg (NVENC/NVDEC),
faster-whisper/CTranslate2, OpenCV, QA e UI/UX.

A tarefa tem **duas partes, nesta ordem**:

- **Parte A — verificação (sem alterar nada).** Confira se o que a pilha de PRs #11 → #16 promete está implementado,
  testado e medido. Entregue o relatório da seção 6 **antes** de qualquer mudança.
- **Parte B — seguimento.** Só depois do relatório, e só nos itens da seção 7, numa branch nova a partir de
  `feature/fase4-melhorias`. Um commit por item, sem push e sem PR: o dono do projeto revisa e aprova cada um.

## 2. Regras que não podem ser quebradas

1. **Nada de merge na `master`** e nada de push sem aprovação explícita do dono.
2. **Não reinicie nem pare o servidor da porta 8000** (é o app em uso, com projetos reais em `data/`). Para testar no
   navegador, suba outra instância na **porta 8001** com dados temporários:
   `AUTOCORTES_DATA=<pasta temporária>` e `AUTOCORTES_OUTPUT_DIR=<outra pasta temporária>`.
   **Não toque na porta 8006** (é outro projeto do usuário, fora deste repositório).
3. **Não grave nada nos projetos reais** (`data/<id>/`): renders de teste vão para uma pasta temporária. Ler é permitido.
4. **Não exponha o servidor na rede sem senha.** O padrão é `127.0.0.1`.
5. **Nada de envio externo** (OpenAI, Anthropic, ACRCloud) sem consentimento; a curadoria por IA não será usada
   (não há chave de API). Não automatize confirmações de direitos autorais.
6. **Não implemente a integração "Pipeline Canal Dark"** nem copie código dela (a licença proíbe redistribuição).
   Não crie nada para driblar Content ID ou detecção de direitos autorais.
7. **Não prometa viralização.** O motor só pode afirmar o que foi medido contra a referência.
8. Seja honesto: o que não conseguir verificar entra no relatório como "não verificado", com o motivo.

## 3. Ambiente

- Windows 11, GPU GTX 1650 (4 GB), Python 3.12 na `.venv`, FFmpeg 9 no PATH (via `winget`).
- Testes: `.venv\Scripts\python.exe -m unittest discover -s tests -t .` (cerca de 7–12 min; 341 testes, 2 pulados).
  O `pytest` também está na `.venv`.
- Não rode a suíte enquanto houver exportação na porta 8000: os testes usam FFmpeg e disputam o processador.
- Variáveis úteis: `AUTOCORTES_ENCODER=auto|x264`, `AUTOCORTES_SELECTION_ENGINE=v1|v2`,
  `AUTOCORTES_AUDIO_LAYER=0`, `AUTOCORTES_MINIMAL=1` (inicializador).

## 4. A pilha de PRs (cada um aponta para o anterior)

| PR | Branch | Commit(s) | O que entrega |
|---|---|---|---|
| #10 | `feature/narrated-batch` | (já revisado antes) | Lote narrado, seguir rosto, webcam + jogo, filtros de cor, legendas precisas |
| #11 | `feature/fase1-ui` | 92b17fe | Página inicial, navegação enxuta, novo projeto em 2 passos |
| #12 | `feature/motor-fase2` | cd0a6f7, e3a9e03 | Auditoria do motor, script de avaliação, quantidade relativa (até 4/h), fusão IA + heurística, motor v2 opcional |
| #13 | `feature/motor-fase3` | f2d524c, dace868 | Camada de áudio (`app/audio_events.py`); "Só o streamer" segue só a bancada |
| #14 | `feature/fase2-fluxo` | 00b406e | Exportar selecionados, progresso do lote, abrir pasta, aviso de vídeo duplicado |
| #15 | `feature/fase3-desempenho` | 69ee890 | Cache de faixas pretas, NVENC com volta para x264, um só Whisper na GPU, importar do computador sem cópia, 👍/👎, "Tentar de novo", inicializador |
| #16 | `feature/fase4-melhorias` | 6c19808 | Perfis de exportação, "Só o streamer" no "Por trecho", inicializador instala IA/GPU, medição de áudio e visual |

Também abertos: #2 (duplicado do #1), #3 (acesso por IP) e #4 (Cloudflare Tunnel), todos contra a `master`.
A revisão deles está em `REVISAO_PRS_ANTIGOS.md`.

Documentos de referência: `ANALISE_MOTOR_CORTES.md` (seções 1 a 9), `CHANGELOG.md` ("Não lançado"), `README.md`,
`REVISAO_PRS_ANTIGOS.md`, `ESTUDO_INSTALADOR_WINDOWS.md`.

## 5. Parte A — o que verificar

Para cada item: diga **como** verificou (leitura, teste, comando, navegador), o resultado e arquivo:linha dos defeitos.

### 5.1 Suíte e qualidade geral
- Rode a suíte inteira e informe o resultado exato. Aponte testes lentos, frágeis ou que dependem da máquina.
- Procure regressões entre PRs: algo de um PR anterior que um posterior desfez (compare com `git diff <base>..<head>`).

### 5.2 Motor de cortes (#12, #13, #16)
- Rode `scripts/avaliar_selecao.py` no projeto de referência (live de 8,5 h: `data/7c572ac2990d413ba8a509ea296f362e`;
  os 18 cortes aprovados estão em `data/4a8838bed3274c46bfc0f9e76425cbbe`; veja `--help` do script) e confira os
  números da seção 8 de `ANALISE_MOTOR_CORTES.md`: R@18 = 0,33 e R@50 = 0,56 com v1 + áudio; v2 pior que v1.
- Confira se o reforço de áudio só vale para trechos com conteúdo (`window_adjustment`, `has_content`) e se a extensão
  do fim do corte não invade a fala seguinte.
- Seção 9 (camada visual **não integrada**): confira o raciocínio e a validação cruzada. O ganho é real ou é ruído de
  uma referência pequena? Os scripts usados estão descritos no texto; refaça se tiver tempo (a passada visual leva
  ~20 min na GPU).
- Avalie o risco apontado no próprio documento: a regra "reação no fim" foi escolhida olhando a mesma referência.

### 5.3 Desempenho (#15)
- `app/media.py`: `detect_letterbox` (cache `cache/letterbox_v1.json`, invalidação pelo carimbo tamanho/data),
  `choose_encoder`/`video_codec_args` (CQ 28 até 30 fps, CQ 30 a 60 fps) e a volta NVENC → x264 em `export_clip`.
  Verifique se a volta apaga o render parcial e se o resultado grava o codificador usado.
- `app/transcription.py`: `_load_model` e `_load_refine_model` reaproveitam o mesmo modelo só quando é GPU +
  `int8_float16`. Procure condições de corrida (dois jobs carregando ao mesmo tempo).
- Importação por caminho (`service.check_path`, `create_from_path`): caminhos relativos, aspas, extensões, arquivo
  inexistente, outro disco (cópia), mesmo disco (hard link), duplicado. **Segurança:** o endpoint aceita qualquer
  caminho absoluto do PC; confirme que só lê arquivos de vídeo e que não há como ler outro tipo de arquivo nem
  escapar da validação (links simbólicos, `\\?\`, caminhos UNC de rede).
- Medições citadas no #15 (faixas pretas 35 s → 11 s → 5,3 s; NVENC 82–95 s → 30–51 s num corte de 62 s): refaça
  pelo menos uma numa cópia temporária de um vídeo.

### 5.4 Interface (#11, #14, #15, #16)
Na instância da porta 8001, com um vídeo curto gerado pelo FFmpeg (`testsrc2` + `sine`):
- Novo projeto nos três modos (enviar arquivo, do computador, link desativado sem `yt-dlp`), com e sem duplicado.
- Exportar selecionados, progresso do lote, cancelar, "Tentar de novo" numa falha simulada.
- 👍/👎, "Esconder rejeitados", "Por que este corte foi sugerido".
- Perfis: aplicar, mudar uma opção (deve voltar a "Personalizado"), salvar, recarregar a página, excluir, nome
  repetido, "Tipo de conteúdo" ao criar o projeto. O perfil precisa continuar coerente quando um **plano de edição**
  define o estilo da legenda.
- Console sem erros; CSP `style-src 'self'` respeitada (sem `style=""` em HTML gerado); largura de celular (375 px).
- Acessibilidade básica: foco visível, `aria-pressed` nos botões de alternar, diálogos fecham com Esc.

### 5.5 Enquadramento (#13, #16)
- "Só o streamer" sem janela de webcam: só a bancada é seguida (`reframe.main_set_mask`).
- "Por trecho" com um trecho "só o streamer" e outro "seguir rosto": o primeiro usa o plano da bancada
  (`track['main_set']`), o segundo segue todos (`media._mixed_graph`). Confira o caso com só um tipo de trecho e o
  cache dos dois planos em `pipeline._cached_track`.

### 5.6 Inicializador Windows (#15, #16)
- `iniciar_windows.bat` precisa continuar ASCII + CRLF (`tests/test_windows_launcher.py`).
- Escolha de pacotes (`DEPS`): `requirements-ai.txt` sempre que existir; `requirements-gpu.txt` se `nvidia-smi`
  responder; `AUTOCORTES_MINIMAL=1` só o básico. O hash em `.venv\.deps-hash` inclui a escolha e o conteúdo dos
  arquivos. Verifique o que acontece numa `.venv` antiga (hash antigo só do `requirements.txt`): vai reinstalar; isso
  pode trocar versões de pacotes já instalados?
- Diagnóstico quando o servidor não sobe (porta 8000 ocupada) e abertura do navegador só depois do `/api/health`.

## 6. Formato do relatório (Parte A)

1. Resumo em até 10 linhas: o estado da pilha e se ela pode seguir para revisão de merge.
2. Tabela: item verificado · como · resultado (passou / falhou / não verificado) · evidência.
3. Defeitos, do mais grave ao menos grave: arquivo:linha, cenário que falha (entrada → resultado errado), sugestão.
4. Medições refeitas, com os números obtidos e a comparação com os publicados.
5. Riscos que não são defeitos (dívida técnica, testes faltando, documentação desatualizada).

## 7. Parte B — seguimento (só depois do relatório)

Branch nova a partir de `feature/fase4-melhorias`, por exemplo `feature/fase5-seguimento`. Um commit por item, com
teste. Pare e pergunte antes de qualquer item que mude o resultado do motor de cortes.

1. **Corrigir os defeitos** confirmados na Parte A (os graves primeiro).
2. **Pedido de senha e aviso de envio grande no Studio** (`app/static/studio.js`), para quando o #3/#4 forem refeitos:
   tratar a resposta 401 (`code: auth_required`) e 413 da Cloudflare com mensagens claras. Não refaça o #3/#4 em si.
3. **Testes que faltam** apontados na Parte A (por exemplo: concorrência no carregamento do Whisper, importação por
   caminho com links simbólicos e caminhos UNC).
4. **Avaliação com o feedback:** documente no README como marcar 👍/👎 num segundo vídeo (podcast ou IRL) e rodar
   `scripts/avaliar_selecao.py --feedback`, para validar a regra de áudio fora da referência original. Não ajuste
   pesos do motor sem essa segunda referência.
5. **Limpeza de caches** (`cache/`, `direction/track_*.json`): tamanho por projeto na interface e um botão para
   apagar, sem apagar vídeo, transcrição nem cortes.

Ao terminar cada item: rode os testes afetados; ao final, a suíte inteira. Atualize `CHANGELOG.md` ("Não lançado").

## 8. Pendente de decisão do dono (não decida sozinho)

- Volume original padrão nos vídeos narrados: hoje 60%; foi perguntado se deve ser 0.
- Fechar o #2 como duplicado do #1 e a ordem de merge da pilha #10 → #16 na `master`.
- Seguir com o instalador (opção B de `ESTUDO_INSTALADOR_WINDOWS.md`: Python embutido + Inno Setup).
