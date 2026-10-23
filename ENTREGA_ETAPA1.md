# Entrega da etapa 1 — AutoCortes Studio Local 2.1.1

Data: 21/10/2026. Continuação direta do ZIP v2.1 recebido. Não é a entrega completa das cinco etapas.
Os 81 testes originais foram preservados; acrescentados 14, totalizando 95.

## Implementação por arquivo

| Arquivo | Alteração e motivo |
|---|---|
| requirements-ai.txt | PyAV >=11,<19: incompatibilidade real com faster-whisper 1.2.1 reproduzida e corrigida |
| app/media.py | Bloqueio na escolha/publicação do nome versionado para impedir colisão entre workers; stderr em memória limitada |
| app/service.py | Snapshot do roteiro na exportação; dedupe inclui o conteúdo do pedido; consentimentos OpenAI persistidos antes da fila e exportados no relatório |
| app/pipeline.py | Usa o snapshot autorizado; roteiro gerado para tempos antigos é rejeitado se o corte mudou durante a chamada |
| app/narration.py | Responses sem armazenamento (`store: false`), respostas malformadas tratadas e tempos infinitos rejeitados |
| app/selection.py | Tipos de score e complete_thought validados estritamente; pesos de score_window preservados |
| app/subtitles.py | Fim de bloco limitado ao fim do corte |
| app/security.py | Origin inválida e Content-Length negativo não provocam erro interno nem passam na validação |
| app/errors.py | Remove chaves configuradas, padrões de chave e caminhos adicionais das mensagens sanitizadas |
| app/main.py, app/static/* | Favicon local; seletor acessível por teclado; confirmação de direitos reiniciada por projeto; opt-in TTS separado |
| iniciar_windows.bat | Reutiliza venv sem depender de py; fallback python; falhas claras; modo sem navegador |
| tests/test_api.py | Range 206/416 e fluxo de geração/edição de roteiro pela API real, com provedor externo simulado |
| tests/test_regressions_v21.py | Segurança, limites temporais, parser, consentimento, snapshots, exportações concorrentes, bloqueio Windows, prioridade e decoder Whisper real |
| .github/workflows/tests.yml | Matriz Windows/Linux, Python 3.10/3.12, FFmpeg, pytest e artefatos de resultados |
| README.md, CHANGELOG.md, AUDITORIA.md | Estado atual e limitações registrados separadamente do histórico |

## Evidências executadas

Suíte original (antes das mudanças), Windows/Python 3.12.14:

```
81 passed, 29 subtests passed in 108.27s (0:01:48)
```

Suíte ampliada:

```
95 passed, 29 subtests passed in 103.79s (0:01:43)
```

API repetida após inclusão do favicon na lista de estáticos:

```
6 passed in 10.02s
```

Logs completos em `evidencias/`. A primeira tentativa dentro do sandbox bloqueou operações temporárias;
a suíte acima foi executada nativamente com as mesmas dependências. Nenhum teste foi ignorado nessa execução Windows.

### Whisper real

O vídeo indicado pelo usuário (Shorts rYRAvZKm9Fk) foi baixado apenas para o teste local e não integra o pacote.
Duração 74,51 s, modelo small, CPU, idioma pt. O primeiro teste falhou com `metadata_errors` no PyAV 19.0.2.
Depois de instalar PyAV 18.1.0: 38,44 s de transcrição, 18 segmentos, 136 palavras e três candidatos.
O primeiro corte (0–17,5 s) foi exportado com sete blocos de legenda e aprovado pela validação real do MP4.
Nenhum timestamp de palavra ou bloco ficou fora dos limites. O quadro `caption-frame.png` permite revisar a renderização.

Não há transcrição de referência, WER, nem anotação manual dos instantes de cada palavra. A conferência de limites
não comprova sincronização perceptual perfeita. O material tem fala e edição prévia; não é representativo de todos os vídeos.
Os pesos de seleção foram preservados para evitar calibrar a heurística com uma única amostra.

### Windows e interface

- `iniciar_windows.bat` iniciou servidor real em 127.0.0.1:8000 usando venv já existente e sem `py`.
- FFmpeg/ffprobe 9.0.2 com libass e libx264 confirmados.
- Arquivos com acentos/espaços, cancelamento e exportação simultânea executados.
- Reexportação com primeiro arquivo bloqueado por CreateFileW (sem compartilhamento de exclusão) passou.
- Flag de prioridade baixa verificada; classe de prioridade observada no SO não foi medida.
- Chrome headless real: 390 px de viewport e 390 px de documento, seletor acionado com Enter, reprodução funcionando,
  zero erros de console. A tabela de direitos mantém rolagem interna horizontal, sem ampliar a página.
- Capturas `ui-390.png` e `ui-desktop.png`. Não substituem teste manual com leitor de tela ou player nativo.

## Decisões

Mantida a arquitetura Python/FastAPI/JavaScript e o motor de exportação validada. Não foi necessário reescrever a UI.
A restrição de PyAV é pequena e testada; substituir o decoder do Whisper seria mudança maior sem necessidade.
O bloqueio de publicação cobre os workers da instância local; múltiplos servidores apontando para o mesmo data/
não são suportados. Snapshots evitam que conteúdo editado após a autorização seja enviado em um job já enfileirado.

Mantidos os modelos configuráveis existentes após consulta às fontes oficiais. Não se trocou o TTS pelo modelo
Realtime indicado na depreciação: isso exige integração de outro protocolo, não troca de identificador.

Fontes oficiais consultadas em 21/10/2026:

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Text to speech](https://developers.openai.com/api/docs/guides/text-to-speech)
- [Text generation — output_text](https://developers.openai.com/api/docs/guides/text)
- [Images and vision — input_image e data URL](https://developers.openai.com/api/docs/guides/images-vision)
- [Depreciações — snapshots mini TTS, retirada em 06/01/2027](https://developers.openai.com/api/docs/deprecations)

## Não verificado e riscos remanescentes

- OpenAI real: sem OPENAI_API_KEY; disponibilidade na conta, geração de voz e envio real de imagens não testados.
  Simulações não comprovam integração ao vivo. O contrato output_text/input_image foi conferido documentalmente.
- Ollama real: conexão local a /api/tags falhou; sem servidor ativo. Casos desligado/JSON inválido permanecem cobertos.
- GPU Whisper, Linux, CI remota e instalação limpa pelo batch: não verificados neste ambiente.
- MP4 em player nativo: não verificado literalmente; bloqueio Windows equivalente e playback no Chrome foram exercitados.
- Correspondência de palavras e decisões de seleção exigem revisão humana; a amostra não sustenta avaliação geral de qualidade.
- CI está configurada, mas só será verificada quando executada em um repositório GitHub.

## Escopo restante do pedido

A etapa 1 tem as pendências externas acima; não deve ser marcada integralmente concluída.
Etapas 2–4 ainda pendentes: rastreamento de rosto/falante, remoção de silêncios, revisão/estilos de legenda,
pré-escuta e orçamento de narração, lote, timeline, prévia exportada, configurações, catálogo, trilha autorizada e PDF.
Upload YouTube não foi implementado; continua condicionado a OAuth explícito.
Etapa 5: workflow CI preparado; instalador Windows ainda não implementado.
