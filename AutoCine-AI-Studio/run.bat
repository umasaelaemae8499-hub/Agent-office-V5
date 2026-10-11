@echo off
REM AutoCine AI Studio launcher for Windows.
REM   run.bat              -> launch the local web UI (http://127.0.0.1:7860)
REM   run.bat doctor       -> environment diagnostics
REM   run.bat create --prompt "..." --input C:\clips
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")

if not exist ".venv\Scripts\python.exe" (
    echo [AutoCine] Creating virtual environment...
    %PY% -m venv .venv || (echo Python 3.11+ is required: https://www.python.org/downloads/ & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1
)

where ffmpeg >nul 2>nul || echo [AutoCine] WARNING: ffmpeg not on PATH. Install with: winget install Gyan.FFmpeg

if not exist ".env" copy ".env.example" ".env" >nul

if "%~1"=="" (
    ".venv\Scripts\python.exe" -m app.main ui
) else (
    ".venv\Scripts\python.exe" -m app.main %*
)
endlocal
