# Publicar o AutoCortes pela Cloudflare

## Por que não dá para hospedar o app "dentro" da Cloudflare

O AutoCortes é um servidor Python (FastAPI) que chama o **FFmpeg**, transcreve áudio com Whisper, grava vídeos
de vários GB em disco e processa por minutos. Os produtos de hospedagem da Cloudflare não comportam isso:

| Produto | Por que não serve |
|---|---|
| **Pages** | Só hospeda sites estáticos (HTML/JS). Não roda Python nem FFmpeg. |
| **Workers** (inclusive Python Workers) | Não executa programas como o FFmpeg, tem 128 MB de memória, tempo de CPU limitado por requisição e nenhum disco local para os vídeos. |
| **Containers** | Exige plano pago, o disco é apagado quando o contêiner para (projetos e cortes se perdem) e CPU/RAM para render e Whisper ficam caros. Seria preciso reescrever o armazenamento para R2. |

O caminho que funciona é o **Cloudflare Tunnel**: o app continua rodando no seu PC (com a sua GPU/CPU, seus
arquivos e o FFmpeg instalado), e o programa `cloudflared` cria uma ligação de saída até a Cloudflare. Quem abre o
endereço `https://...` passa pela Cloudflare e chega ao app sem você abrir porta no roteador. O PC precisa ficar
ligado e com o AutoCortes aberto enquanto você quiser usar de fora.

## Uso rápido (sem conta, endereço temporário)

1. Instale o `cloudflared` (uma vez), no Prompt de Comando ou PowerShell:

   ```bat
   winget install --id Cloudflare.cloudflared
   ```

2. Dê dois cliques em **`publicar_cloudflare_windows.bat`**. Ele pede uma senha, abre o AutoCortes em outra
   janela e mostra um endereço como `https://palavras-sorteadas.trycloudflare.com`.
3. Abra esse endereço no celular ou em outro PC. O navegador pede usuário e senha: use **qualquer usuário** e a
   senha que você definiu.

Para não digitar a senha toda vez: `setx AUTOCORTES_PASSWORD "sua-senha-longa"` (vale para os próximos terminais).
Use só letras, números, `-` e `_` na senha, pois o `cmd` do Windows trata alguns símbolos de forma especial.

O endereço `trycloudflare.com` muda a cada execução e é para uso pessoal/testes (sem garantia de disponibilidade).

## Endereço fixo no seu domínio (precisa de conta Cloudflare)

Estes passos são feitos por você no painel da Cloudflare, porque dependem da sua conta:

1. Crie uma conta gratuita em <https://dash.cloudflare.com> e adicione um domínio seu (o DNS do domínio precisa
   estar na Cloudflare).
2. Em **Zero Trust → Networks → Tunnels**, clique em **Create a tunnel**, escolha **Cloudflared**, dê um nome
   (ex.: `autocortes`) e copie o **token** mostrado no comando de instalação (o texto longo depois de `--token`).
3. Na aba **Public Hostname** do túnel, adicione por exemplo `cortes.seudominio.com` com serviço
   **HTTP** e URL `localhost:8000`.
4. No PC, guarde o token e o endereço (uma vez):

   ```bat
   setx CLOUDFLARE_TUNNEL_TOKEN "cole-o-token-aqui"
   setx AUTOCORTES_PUBLIC_HOST "cortes.seudominio.com"
   setx AUTOCORTES_PASSWORD "sua-senha-longa"
   ```

5. Feche e abra o terminal e execute `publicar_cloudflare_windows.bat`. Acesse `https://cortes.seudominio.com`.

O token dá controle do túnel: não o compartilhe nem o coloque em arquivos do projeto.

### Camada extra opcional: Cloudflare Access

Com domínio próprio, você pode exigir login da Cloudflare **antes** de qualquer acesso chegar ao seu PC
(gratuito até 50 usuários): **Zero Trust → Access → Applications → Add an application → Self-hosted**, informe
`cortes.seudominio.com` e crie uma política **Allow** com **Emails** = o seu e-mail. Ao abrir o site, a Cloudflare
envia um código por e-mail; depois disso o navegador ainda pede a senha do AutoCortes. Mantenha as duas.

## Segurança

- O app continua escutando só em `127.0.0.1`. Nada é exposto na rede local nem no roteador; só o `cloudflared`
  conversa com a internet. Fechar a janela do túnel tira o AutoCortes do ar.
- A senha é obrigatória: o script não publica sem ela. Com a senha definida, ela também é pedida em
  `http://127.0.0.1:8000` neste PC.
- Só os endereços do túnel são aceitos como Host (`.trycloudflare.com` no modo rápido ou o seu
  `AUTOCORTES_PUBLIC_HOST`); a proteção contra outros sites (Origin/CSRF) continua ativa.
- Após várias senhas erradas o visitante é bloqueado por alguns minutos. O bloqueio usa o IP real de quem tentou
  (informado pela Cloudflare), então um estranho não bloqueia o seu acesso.

## Limites

- **Envio de arquivos: até 100 MB por vídeo** pela internet (limite do plano gratuito da Cloudflare). Para vídeos
  maiores, use a importação por link (YouTube) ou envie o arquivo direto no PC. A interface avisa quando o envio
  passa desse limite.
- Velocidade de envio e de download dos cortes depende da internet do seu PC (upload da sua conexão).
- Processar continua usando o seu PC: deixe-o ligado e sem entrar em suspensão.
