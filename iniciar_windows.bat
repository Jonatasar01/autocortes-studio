@echo off
setlocal
rem Servidor exclusivamente local; reutiliza uma venv existente.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto dependencies
where py >nul 2>&1
if not errorlevel 1 (
 py -3 -m venv .venv
 goto checkvenv
)
where python >nul 2>&1
if errorlevel 1 goto nopython
python -m venv .venv
:checkvenv
if not exist .venv\Scripts\python.exe goto nopython
:dependencies
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto nopython
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
:nopython
echo E necessario Python 3.10 ou superior, com venv.
if not "%AUTOCORTES_NO_BROWSER%"=="1" pause
exit /b 1
:installerror
echo Falha ao instalar dependencias. Verifique a conexao e tente novamente.
if not "%AUTOCORTES_NO_BROWSER%"=="1" pause
exit /b 1
