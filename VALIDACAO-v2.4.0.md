# Validação da versão 2.4.0

Data: 23/10/2026.

- Instalação com uv.lock e todos os extras concluída no Windows.
- API local respondeu versão 2.4.0; FFmpeg, FFprobe, libass, libx264 e transcrição disponíveis.
- Chrome desktop 1440px e celular 390px: sem erros JavaScript; sem overflow horizontal no celular. Perfil Shorts aplicado pela interface.
- Link fornecido pelo usuário importado realmente do YouTube. Whisper small processou 74,513 segundos de áudio. CUDA não carregou cublas64_12.dll; a alternativa CPU concluiu a transcrição.
- Piloto automático selecionou dois cortes e concluiu duas exportações validadas. Nenhuma chave de IA externa foi utilizada.
- Navegador abriu o projeto, apresentou dois cortes na linha de pré-visualização e baixou ZIP válido de 7.505.491 bytes (HTTP 200).
- FilmCraft 0.12.0: teste sintético com áudio e múltiplas legendas por corte produziu render validado; a integração pela fila também gerou .fcproj, render e SRT do projeto real, sem erro.
- Testes de consentimento verificam que o perfil narrado exige autorização prévia; aplicar Shorts não altera direitos nem autoriza serviços externos.

Não validados: chamadas pagas OpenAI/Anthropic, Docker, aceleração GPU. FilmCraft permanece opcional, com limitações CFR e formato original descritas no guia. A interface oferece pré-visualização por corte, não edição multifaixa livre.

O ZIP de distribuição não inclui ambiente virtual, vídeos importados, dados de usuário ou executável FilmCraft. O servidor de demonstração usa a cópia em work/v240 e permanece em http://127.0.0.1:8005/.

Suíte completa pytest: 167 testes e 42 subtestes aprovados em 216,49 segundos. Log em evidencias/pytest-v240.log.
