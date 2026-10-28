@echo off
setlocal
rem Servidor exclusivamente local; reutiliza uma venv existente.
rem Python: prefere o 3.12 (py -3.12). Aceita 3.10 a 3.12 (as versoes usadas pelo CI do projeto).
rem Versoes mais novas, como 3.15, ainda nao tem pacotes prontos para todas as dependencias.
cd /d "%~dp0"
rem Limite de envio de video em GB (o padrao do app e 4). Uma variavel ja definida no sistema tem prioridade.
if not defined MAX_UPLOAD_GB set MAX_UPLOAD_GB=50
rem Duracao maxima do video em horas (padrao 6, maximo 48).
if not defined AUTOCORTES_MAX_SOURCE_HOURS set AUTOCORTES_MAX_SOURCE_HOURS=12
if exist .venv\Scripts\python.exe goto checkvenv
where py >nul 2>&1
if errorlevel 1 goto trypath
call py -3.12 -m venv .venv >nul 2>&1
if exist .venv\Scripts\python.exe goto checkvenv
:trypath
where python >nul 2>&1
if errorlevel 1 goto nopython
call python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto badpython
call python -m venv .venv
:checkvenv
if not exist .venv\Scripts\python.exe goto nopython
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto badvenv
if not exist .venv\.deps-ok (
 .venv\Scripts\python.exe -m pip install -r requirements.txt
 if errorlevel 1 goto installerror
 type nul > .venv\.deps-ok
 del .venv\.deps-hash >nul 2>&1
)
rem Dependencias mudaram desde a ultima instalacao (atualizacao do AutoCortes): instala o que falta.
rem Sem hash anterior, so registra o atual. O hash so e gravado depois de uma instalacao bem-sucedida.
.venv\Scripts\python.exe -c "import hashlib,pathlib,sys; h=hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest(); p=pathlib.Path('.venv/.deps-hash'); old=p.read_text().strip() if p.exists() else ''; old or p.write_text(h); sys.exit(3 if old and old != h else 0)" >nul 2>&1
if errorlevel 3 goto updatedeps
:depsok
where ffmpeg >nul 2>&1
if errorlevel 1 echo AVISO: ffmpeg ausente no PATH.
where ffprobe >nul 2>&1
if errorlevel 1 echo AVISO: ffprobe ausente no PATH.
rem O navegador so abre quando o servidor responder (antes abria cedo demais e mostrava erro de conexao).
if not "%AUTOCORTES_NO_BROWSER%"=="1" start "" /b powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "for($i=0;$i -lt 90;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8000/api/health | Out-Null; Start-Process 'http://127.0.0.1:8000'; exit}catch{Start-Sleep -Seconds 1}}"
echo AutoCortes Studio: abrindo em http://127.0.0.1:8000 (deixe esta janela aberta enquanto usa o app).
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
set RC=%errorlevel%
if "%RC%"=="0" exit /b 0
echo.
echo O AutoCortes parou (codigo %RC%). O que verificar:
netstat -ano | findstr /c:"127.0.0.1:8000" | findstr LISTENING >nul && echo  - A porta 8000 ja esta em uso: outro AutoCortes pode estar aberto. Feche-o ou acesse http://127.0.0.1:8000
echo  - Se aparecer "ModuleNotFoundError", apague o arquivo .venv\.deps-ok e execute de novo para reinstalar.
echo  - As mensagens acima mostram o motivo completo.
if not "%AUTOCORTES_NO_BROWSER%"=="1" pause
exit /b %RC%
:updatedeps
echo Dependencias atualizadas: instalando o que mudou...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto installerror
.venv\Scripts\python.exe -c "import hashlib,pathlib; pathlib.Path('.venv/.deps-hash').write_text(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())"
goto depsok
:badvenv
echo.
echo ERRO: a pasta .venv deste projeto foi criada com uma versao de Python nao suportada.
for /f "delims=" %%v in ('.venv\Scripts\python.exe -c "import sys; print(sys.version.split()[0])" 2^>nul') do echo Versao da .venv: %%v
echo Apague a pasta .venv, instale o Python 3.12 e execute este arquivo de novo.
goto install312
:badpython
echo.
for /f "delims=" %%v in ('python -c "import sys; print(sys.version.split()[0])" 2^>nul') do echo O Python encontrado no PATH e a versao %%v, que nao e suportada.
echo Se for o atalho da Microsoft Store, ele nao serve: instale o Python 3.12 abaixo.
:nopython
echo.
echo ERRO: Python 3.12 nao encontrado.
echo O AutoCortes usa Python 3.12 (aceita 3.10 a 3.12). Versoes mais novas, como 3.15, ainda nao
echo tem pacotes prontos para todas as dependencias e a instalacao falha.
:install312
echo.
echo Instale o Python 3.12 e execute este arquivo de novo. Opcoes:
echo   - no terminal:  py install 3.12
echo   - ou baixe em:  https://www.python.org/downloads/windows/
if not "%AUTOCORTES_NO_BROWSER%"=="1" pause
exit /b 1
:installerror
echo Falha ao instalar dependencias. Verifique a conexao e tente novamente.
if not "%AUTOCORTES_NO_BROWSER%"=="1" pause
exit /b 1
