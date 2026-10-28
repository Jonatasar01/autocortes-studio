# Revisão dos PRs #2, #3 e #4

Os três apontam para a `master`, que ficou muitos PRs atrás da pilha atual (#5 → #15). Eles ainda estão
"mescláveis" contra a `master`, mas vão conflitar com a pilha em `app/main.py`, `app/security.py`,
`app/config.py`, `iniciar_windows.bat`, `README.md` e `CHANGELOG.md`.

## #2 — "Add Studio — new manual editing interface"
- **Mesmo commit de cabeça do #1** (`feature/studio-manual-editor-ui`), como o próprio PR diz. Não traz nada além do #1,
  e a interface Studio evoluiu muito na pilha (#5 → #15).
- **Recomendação:** fechar como duplicado do #1.

## #3 — "Acesso por IP na rede local, com senha obrigatória"
O que está bom:
- Continua só em `127.0.0.1` por padrão; **recusa iniciar exposto sem `AUTOCORTES_PASSWORD`** (atende a regra de não
  expor o servidor sem autenticação).
- Comparação da senha em tempo constante (`hmac.compare_digest`), bloqueio temporário depois de falhas seguidas,
  `cache-control: no-store` nas respostas de recusa, testes de inicialização.

Problemas e riscos:
1. **Senha em texto quase aberto na rede local.** HTTP Basic sem HTTPS manda a senha em base64 a cada requisição:
   qualquer aparelho que observe o Wi-Fi (rede aberta, hotspot) consegue lê-la. O README avisa para usar HTTPS só na
   internet; vale avisar também para redes que não são de confiança.
2. **`AUTOCORTES_ALLOWED_HOSTS=*`** desliga a proteção de Host (DNS rebinding). Com senha o risco cai, mas o ideal é
   não aceitar `*` quando o servidor está exposto.
3. **Bloqueio por IP pode ser zerado:** o dicionário de falhas é apagado inteiro ao passar de 10 000 IPs, o que zera o
   bloqueio de todos (só importa na internet; na rede local é pouco provável).
4. **Conflitos com a pilha:** `iniciar_windows.bat` mudou na Fase 3 (verificação do `/api/health`, hash das
   dependências) e `app/security.py`/`app/main.py` mudaram em vários PRs.

**Recomendação:** refazer em cima da pilha depois que ela entrar na `master` (rebase), tratando os itens 1 e 2.

## #4 — "Acesso pela internet via Cloudflare Tunnel (Windows)"
Depende do #3 (parte da branch dele).

O que está bom:
- O app continua escutando só em `127.0.0.1`; o `cloudflared` faz uma ligação de saída (nada aberto no roteador).
- Senha obrigatória; o bloqueio por senha errada usa o IP real (`CF-Connecting-IP`) quando a conexão vem do
  `cloudflared` local, então um visitante não bloqueia o dono.
- Aviso claro quando a Cloudflare recusa envios acima de 100 MB.

Problemas e riscos:
1. **Interface antiga:** o aviso de 100 MB e o tratamento do pedido de senha foram feitos em `app/static/app.js`
   (editor clássico). A interface principal agora é o Studio (`studio.js`), que não tem esse tratamento; enviar um
   vídeo grande pelo túnel falharia sem explicação. A importação "Do computador" (Fase 3) não passa pelo túnel e
   resolve o caso de vídeos grandes no próprio PC.
2. **`CF-Connecting-IP` aceito de qualquer processo local:** qualquer programa no PC pode mandar esse cabeçalho e
   trocar de "IP" a cada tentativa, escapando do bloqueio. O risco é baixo (exige acesso ao PC), mas dá para limitar
   a confiança ao processo do túnel (por exemplo, uma porta só para o `cloudflared`).
3. **Sufixo `.trycloudflare.com` aceita qualquer túnel rápido** como Host. Com senha, é aceitável; no túnel fixo, use
   o domínio exato.
4. Mesmos conflitos do #3.

**Recomendação:** mesclar só depois do #3 refeito, levando o aviso de 100 MB e o pedido de senha para o `studio.js`.

## Ordem sugerida
1. Revisar e mesclar a pilha #5 → #15 (cada uma na anterior) e depois na `master`.
2. Fechar o #2 como duplicado do #1.
3. Refazer #3 e #4 sobre a `master` atualizada, com as correções acima.
