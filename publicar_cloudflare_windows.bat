@echo off
setlocal
rem Publica o AutoCortes na internet por um Cloudflare Tunnel (cloudflared).
rem O app continua rodando NESTE PC e escutando so em 127.0.0.1; quem fala com a internet e o cloudflared.
rem Sem CLOUDFLARE_TUNNEL_TOKEN: tunel rapido, endereco https://xxxx.trycloudflare.com sorteado a cada vez.
rem Com CLOUDFLARE_TUNNEL_TOKEN e AUTOCORTES_PUBLIC_HOST: tunel fixo no seu dominio (ver CLOUDFLARE.md).
rem A senha AUTOCORTES_PASSWORD e obrigatoria (o script pergunta se nao estiver definida).
cd /d "%~dp0"
where cloudflared >nul 2>&1
if errorlevel 1 goto nocloudflared
if "%AUTOCORTES_PORT%"=="" set AUTOCORTES_PORT=8000
set AUTOCORTES_HOST=127.0.0.1
if not "%AUTOCORTES_PASSWORD%"=="" goto haspassword
echo O AutoCortes vai ficar acessivel pela internet e precisa de uma senha.
echo Use uma senha longa (12 caracteres ou mais), so com letras, numeros, - e _, que voce nao usa em outro lugar.
set /p AUTOCORTES_PASSWORD=Senha de acesso: 
if "%AUTOCORTES_PASSWORD%"=="" goto nopassword
:haspassword
if "%CLOUDFLARE_TUNNEL_TOKEN%"=="" goto quick
if "%AUTOCORTES_PUBLIC_HOST%"=="" goto nopublichost
set "AUTOCORTES_ALLOWED_HOSTS=%AUTOCORTES_ALLOWED_HOSTS%,%AUTOCORTES_PUBLIC_HOST%"
call :startapp
echo.
echo Tunel fixo: abra https://%AUTOCORTES_PUBLIC_HOST% (qualquer usuario + a senha definida).
echo Feche esta janela para tirar o AutoCortes da internet.
echo.
set "TUNNEL_TOKEN=%CLOUDFLARE_TUNNEL_TOKEN%"
cloudflared tunnel --no-autoupdate run
exit /b %errorlevel%
:quick
set "AUTOCORTES_ALLOWED_HOSTS=%AUTOCORTES_ALLOWED_HOSTS%,.trycloudflare.com"
call :startapp
echo.
echo Tunel rapido: procure abaixo o endereco https://....trycloudflare.com e abra no celular ou em outro PC.
echo Entre com qualquer usuario + a senha definida. O endereco muda toda vez que este script e iniciado.
echo Feche esta janela para tirar o AutoCortes da internet.
echo.
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:%AUTOCORTES_PORT%
exit /b %errorlevel%
:startapp
rem Abre o app em outra janela com as mesmas variaveis (senha e hosts aceitos).
set AUTOCORTES_NO_BROWSER=1
start "AutoCortes" cmd /k iniciar_windows.bat
echo AutoCortes iniciando em outra janela (http://127.0.0.1:%AUTOCORTES_PORT% neste PC).
echo Se a primeira visita mostrar erro 502, aguarde o app terminar de abrir e recarregue.
exit /b 0
:nocloudflared
echo.
echo ERRO: cloudflared nao encontrado.
echo Instale com:  winget install --id Cloudflare.cloudflared
echo Depois feche e abra o terminal (ou este arquivo) de novo.
pause
exit /b 1
:nopassword
echo ERRO: sem senha o AutoCortes nao e publicado na internet.
pause
exit /b 1
:nopublichost
echo ERRO: CLOUDFLARE_TUNNEL_TOKEN definido, mas falta AUTOCORTES_PUBLIC_HOST (ex.: cortes.seudominio.com).
echo Use o mesmo nome configurado em "Public Hostname" do tunel no painel da Cloudflare.
pause
exit /b 1
