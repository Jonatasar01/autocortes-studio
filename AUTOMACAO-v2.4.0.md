# AutoCortes Studio Local 2.4.0 — automação

Esta versão conecta importação, transcrição, seleção e exportação na central de automação. Os arquivos existentes permanecem na versão anterior; a nova instalação usa sua própria pasta data.

## Instalação

Requisitos: Python 3.10+, uv, FFmpeg/FFprobe no PATH. Para links do YouTube, Node.js no PATH pode ser necessário para desafios do site. Execute iniciar_automacao_windows.bat ou bash iniciar_automacao_linux_mac.sh. Ambos usam uv.lock e os extras ai e links. A primeira transcrição pode baixar o modelo Whisper small. Alternativa: pip install -r requirements-automation.txt em um ambiente virtual.

## Uso

1. Para Recap com IA, configure e autorize primeiro a narração em Configurações. Aplique Shorts automáticos, Distribuição multiformato ou Recap com IA.
2. Envie um arquivo ou selecione Link do YouTube / Shorts. Informe os direitos corretamente.
3. Acompanhe importação, análise e exportações na fila. Falhas de importação/análise permitem nova tentativa; tarefas em andamento podem ser canceladas.
4. Revise os cortes na linha de pré-visualização e baixe vídeos, legendas ou o pacote ZIP.

O piloto pode pausar para revisão de direitos ou não selecionar nenhum corte que atenda à nota mínima. Perfis se aplicam aos próximos vídeos; o projeto guarda suas opções originais. Revogar consentimento impede novas etapas automáticas que precisem dele.

## IA

A transcrição usa faster-whisper local. Sem provedor externo, a seleção usa heurísticas locais. Curadoria OpenAI/Anthropic e roteiro/voz OpenAI precisam de chave por variável de ambiente e consentimento em Configurações. Não há chaves incluídas; aplicar perfil não autoriza serviços externos. Chamadas pagas não foram validadas nesta entrega.

## Montagem FilmCraft opcional

Defina FILMCRAFT_CLI para o executável CLI instalado. O botão de montagem entra na fila, gera projeto nativo .fcproj, vídeo e SRT a partir da transcrição. A ponte exige material CFR compatível e preserva o formato original. O projeto nativo referencia a mídia local; mover para outra máquina pode exigir religar a mídia. Não é um editor multifaixa completo no navegador.

## Limites

Links suportados: um vídeo público YouTube/Shorts por vez, até 1080p, sem login. Restrições do site podem impedir downloads. Não há importação universal de redes sociais. O serviço escuta somente localhost e não possui autenticação para exposição pública. Apenas uma instância pode usar a mesma pasta de dados. Docker está incluído, mas não foi executado neste ambiente Windows.
