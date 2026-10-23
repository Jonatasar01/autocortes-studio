# Prompt de evolução para Codex ou Claude Code

Você é uma equipe composta por arquiteto de software, engenheiro Python/video, especialista em visão computacional, designer UI/UX, engenheiro de testes, especialista em segurança e especialista em políticas de plataformas de vídeo.

**Missão:** evoluir o projeto EXISTENTE `AutoCortes Studio Local` em um aplicativo desktop realmente utilizável, com edição semiautomática de cortes, legendas, revisão de direitos autorais e fluxo de publicação, preservando suas funcionalidades e testes. **Não entregue apenas wireframes, mocks, pseudocódigo ou uma nova aplicação que não execute.**

## Princípios e restrições

- Windows 10/11 prioritário, suporte secundário macOS/Linux.
- Priorize processamento local/offline; chaves API opcionais, nunca obrigatórias para o uso básico.
- O Codex ou Claude Code **constrói o software**. Em execução normal, o usuário não precisa deles para editar vídeos. O processamento pode usar Ollama e faster-whisper locais.
- Não envie vídeos, áudio, documentos de licença ou transcrições para serviços externos por padrão.
- Never invent Content ID findings, rights ownership, music ID, permission or certainty of monetization.
- Sem mecanismos para camuflar trechos ou contornar copyright/detecção automatizada (pitch, crop ou inversão com propósito de evasão).
- Direitos autorais, Content ID e elegibilidade de monetização são **três questões distintas**. Trate-as em telas e relatórios separados.
- Sistemas que verificam obras conhecidas necessitam base comparativa autorizada. Se indisponível, apresentar `NÃO VERIFICADO`, não `LIBERADO`.
- Respeite direitos de uso comercial, territórios, atribuição, tipo de plataforma, prazo e restrições contratuais.

## Funcionalidades finais esperadas

### 1. Biblioteca e processamento
- Importação MP4, MKV, MOV, AVI, WEBM, legendas SRT/VTT e múltiplos arquivos em lote.
- Projeto persistente, metadados por arquivo, miniaturas e waveform, tags, pasta do projeto, lixeira, auto-save, logs e recuperação de falha.
- Fila com jobs canceláveis, pausa/retomada, GPU quando disponível, limites de RAM/VRAM, indicadores de tempo e progresso, custo de armazenamento e tratamento de falha individual.
- Validação de arquivos e codecs via ffprobe, caminhos seguros e limite de tamanho configurável.

### 2. IA de seleção dos melhores trechos
- Transcrever por faster-whisper com timestamps de palavras, VAD e identificação opcional de locutores.
- Identificar troca de cenas (PySceneDetect), risos/aplausos como sinais opcionais, silêncio, turnos de fala e picos de interesse.
- Gerar candidatos por *unidades semânticas completas* preservando o contexto, sem fragmentar a fala.
- Classificar os candidatos por gancho nos primeiros segundos, clareza, assunto, começo/meio/fim, autenticidade e retenção estimada **(estimativa, não promessa de viralização)**.
- Usar Ollama local com saída JSON estruturada e validar schema, ou heurísticas locais quando indisponível.
- Detectar clipes repetidos e impedir que cortes propostos sobreponham-se excessivamente.
- Presets de duração 15/30/45/60/90 s, quantidade e plataforma; comparar versões A/B.

### 3. Editor audiovisual real
- Timeline visual com scrubber, in/out frame-accurate, arraste, trim, split, undo/redo, edição não destrutiva por projeto JSON e preview responsivo.
- Templates 9:16, 1:1, 4:5, 16:9, resolução 720p/1080p/4K conforme fonte.
- Auto-reframe com detecção/rastreamento de rosto/falante, respeitando o enquadramento, com revisão manual e fallback letterbox.
- Legenda dinâmica sincronizada por palavras, múltiplos estilos, contraste seguro, proteção da safe area e edição da transcrição.
- Limpeza de áudio, normalização EBU R128, trilhas com direitos documentados, transições discretas, zoom pontual contextual e correção básica de cor.
- Renderização por FFmpeg com progresso, presets, vídeos sem áudio, SRT/VTT e preview do MP4 real exportado.

### 4. Módulo profissional de copyright
- **Origem de cada ativo:** próprio, licenciado, Creative Commons (atribuição/uso comercial/derivações), domínio público comprovado, material de terceiros ou não confirmado.
- Evidências: detentor, licença, comprovante e link, plataforma permitida, uso comercial, expiração e território.
- Analise separadamente: vídeo principal, música de fundo, voz, imagens inseridas, memes, logos, trechos de obras audiovisuais e imagens de banco.
- Detecção opcional de músicas por provedor legítimo (ex.: ACRCloud) **somente quando usuário configurar credenciais**. Marcar correspondência como indício de obra, e jamais prova definitiva da titularidade ou infração. Não instalar/habilitar reconhecimento remoto sem opt-in.
- Comparação opcional por hash/fingerprint contra um catálogo **local** de ativos próprios/licenciados; ausência de match = desconhecido.
- Classificação por itens `documentado`, `revisar`, `não autorizado informado`, `não verificado` com mensagens explicativas, offsets temporais e ação recomendada (obter licença, pedir autorização, substituir por ativo autorizado, remover trecho, revisão jurídica).
- **NÃO** mostrar selo 'copyright free', '100% seguro' ou previsão definitiva de claim. Evitar porcentagem inventada de copyright.
- Relatório de evidências em JSON/PDF opcional, anexando comprovantes, timestamps e trilha de auditoria por projeto.
- Em YouTube, permitir upload privado ou não listado **somente com autorização OAuth explícita**, informando o usuário para conferir o painel `Verificações` do YouTube Studio. Nunca fingir que houve verificação se não houve.

### 5. Política de conteúdo reutilizado e monetização (separadas de copyright)
- Alertar que licenças e ausência de reivindicações não equivalem a elegibilidade no YouTube Partner Program.
- Checklist editável de valor original: apresentação, comentário próprio, análise, contexto novo, narração, edição substantiva — informação auxiliar, não parecer definitivo.
- Não gerar apenas compilações de terceiros como recomendação de monetização.
- Manter informações de políticas versionadas, com fonte e data, e não assumir que IA pode determinar resultado de análise humana.

### 6. Interface e UX
- Evoluir design dark profissional já existente, com sidebar, dashboard, biblioteca, editor de timeline, inspector de corte, preview e painel Direitos.
- Um assistente de importação simples e acessível; teclado, mensagens de progresso/cancelamento, feedback de erro, indicadores sem promessas absolutas.
- Uso desktop responsivo com atalhos de teclado e suporte a vídeos longos.

### 7. Segurança e arquitetura
- Arquitetura em camadas: API, serviço de mídia, transcrição, ranking, direitos, persistência, render queue e adaptadores externos opcionais.
- Backend Python FastAPI; SQLite + SQLAlchemy para histórico; Pydantic para DTO; front-end React/TypeScript/Vite caso melhore editor. Empacotamento com instalador Windows (Tauri/Electron ou alternativa justificada), sem servidor aberto na rede.
- Defesas contra command injection (subprocess `shell=False`), path traversal, upload malicioso, zip bombs, DoS de arquivo gigante, CORS inseguro e vazamento de token.
- Secrets em variável de ambiente/armazenamento protegido. Nunca commitar dados de clientes/vídeos.
- Testes unitários, testes de integração com vídeo de amostra original gerado por FFmpeg e smoke tests de interface.

## Critérios de aceite

1. Rodar em Windows com instalação documentada, sem API paga.
2. Upload e análise de 1 vídeo de teste, renderizar ao menos 3 cortes quando o material comportar.
3. Legendas alinhadas sem cortar falas no meio; edição de título e tempos; exportar 9:16 e 16:9.
4. Reiniciar aplicativo e recuperar projetos sem corrupção; relatório de copyright com origens não confirmadas claramente sinalizadas.
5. Usuário pode **substituir música ou trecho identificado como sem licença** por ativo autorizado e reexportar (não camuflar para fugir do detector).
6. Testar casos: vídeo sem áudio, curto, longo, arquivo inválido, vídeo vertical, falha de GPU, transcrição ausente, Ollama desligado, FFmpeg sem libass, licença desconhecida e conexão offline.
7. Executar `pytest`, formatador e lint; criar README e changelog; entregar código funcional e instruções para instalação, evitando criar apenas um plano.

## Ordem de execução obrigatória

1. Auditar o código existente; documentar bugs, dívida técnica e caminhos reais do MVP.
2. Corrigir os bugs de base e criar testes de regressão.
3. Implementar persistência, fila, progresso e cancelamento.
4. Evoluir transcrição, segmentação semântica e timeline.
5. Implementar módulo Direitos com estados verificáveis e provedores externos opcionais.
6. Implementar auto-reframe, legenda dinâmica, áudio e prévia fiel.
7. Refinar o design e empacotar para Windows.
8. Testar ponta a ponta, documentar dependências e apresentar limitações remanescentes.

Ao concluir cada etapa, entregar o que foi de fato implementado, evidências dos testes, principais decisões técnicas e limites. Nunca afirmar que uma etapa está pronta sem ter executado o teste correspondente.
