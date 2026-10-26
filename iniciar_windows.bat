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
)
where ffmpeg >nul 2>&1
if errorlevel 1 echo AVISO: ffmpeg ausente no PATH.
where ffprobe >nul 2>&1
if errorlevel 1 echo AVISO: ffprobe ausente no PATH.
if not "%AUTOCORTES_NO_BROWSER%"=="1" start "" http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%
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
