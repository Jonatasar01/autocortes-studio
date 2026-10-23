@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
 echo Instale uv e FFmpeg antes de executar. Consulte AUTOMACAO-v2.4.0.md.
 pause
 exit /b 1
)
uv sync --locked --extra ai --extra links
if errorlevel 1 exit /b 1
if "%PORT%"=="" set PORT=8000
echo Abra http://127.0.0.1:%PORT%
uv run --no-sync python -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
exit /b %errorlevel%
