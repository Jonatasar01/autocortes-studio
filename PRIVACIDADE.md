# Política de Privacidade — AutoCortes Studio

Última atualização: 31 de outubro de 2026.

O AutoCortes Studio é um programa que roda no computador de quem o usa. Ele transforma vídeos longos (lives,
podcasts) em cortes curtos. Não existe servidor do AutoCortes na internet: o programa não tem conta de usuário,
não coleta dados de uso e não envia nada para os autores.

## O que fica no seu computador

Vídeos, transcrições, cortes, configurações e chaves de acesso ficam só na pasta de dados do programa, no próprio
computador. Nada disso é enviado para os autores do AutoCortes.

## Integração com o YouTube

Quando você conecta a sua conta do YouTube:

- O login acontece na página do próprio Google. O AutoCortes nunca vê a sua senha.
- A permissão pedida é só a de **enviar vídeos** (`youtube.upload`). O programa não lê, não altera e não apaga
  outros vídeos, comentários, inscritos ou dados do canal.
- O token de acesso fica guardado só no seu computador e é usado apenas quando você manda publicar um corte.
- Vão para o YouTube somente o vídeo que você escolheu publicar e o título, a descrição, as tags e a visibilidade
  que você confirmou na tela.
- Para desconectar, use **Configurações → YouTube → Desconectar** no AutoCortes; o acesso é revogado no Google. Você
  também pode remover o acesso em https://myaccount.google.com/permissions.

O uso de informações recebidas das APIs do Google segue a
[Política de Dados do Usuário dos Serviços de API do Google](https://developers.google.com/terms/api-services-user-data-policy),
incluindo os requisitos de uso limitado. O uso do YouTube está sujeito aos
[Termos de Serviço do YouTube](https://www.youtube.com/t/terms) e à
[Política de Privacidade do Google](https://policies.google.com/privacy).

## Outros serviços opcionais

Só quando você ativa e configura:

- **Twitch:** o número da live é enviado à API oficial da Twitch para listar os clipes públicos feitos dela.
- **Google Gemini:** com o seu consentimento, o texto da transcrição é enviado para sugerir cortes. Nunca o vídeo
  ou o áudio.

## Contato

Dúvidas: abra uma issue em https://github.com/Jonatasar01/autocortes-studio/issues.
