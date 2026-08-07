@echo off
setlocal
title AI Job Applier Agent
echo ==========================================
echo    Starting AI Job Applier Agent...
echo ==========================================

cd /d "%~dp0backend"
set "VENV_PYTHON=%CD%\venv\Scripts\python.exe"
set "PORT=%~1"
if not defined PORT set "PORT=8001"

"%VENV_PYTHON%" --version >nul 2>nul
if errorlevel 1 goto repair_venv
goto environment_ready

:repair_venv
echo The project virtual environment is missing or invalid. Repairing it...
set "BOOTSTRAP_PYTHON="
where python >nul 2>nul
if not errorlevel 1 (
    python --version >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
)

if not defined BOOTSTRAP_PYTHON (
    set "CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if exist "%CODEX_PYTHON%" set "BOOTSTRAP_PYTHON=%CODEX_PYTHON%"
)

if not defined BOOTSTRAP_PYTHON (
    echo Error: A working Python 3.10+ installation was not found.
    echo Install Python from python.org, then run this script again.
    pause
    exit /b 1
)

"%BOOTSTRAP_PYTHON%" -m venv --clear "%CD%\venv"
if errorlevel 1 goto setup_failed
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto setup_failed
"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto setup_failed
"%VENV_PYTHON%" -m playwright install chromium
if errorlevel 1 goto setup_failed
echo Environment repair complete.

:environment_ready
echo Python environment verified.
echo Launching FastAPI Backend...
echo Access the Dashboard at http://127.0.0.1:%PORT%/
echo Press Ctrl+C to shut down the agent.

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "$ready=$false; 1..30 | ForEach-Object { if (-not $ready) { Start-Sleep 1; try { $v=Invoke-RestMethod 'http://127.0.0.1:%PORT%/api/version' -TimeoutSec 1; if ($v.build -eq '20260806.2') { $ready=$true; Start-Process 'http://127.0.0.1:%PORT%/?build=20260806.2' } } catch {} } }"
"%VENV_PYTHON%" -m uvicorn app:app --host 127.0.0.1 --port %PORT% --reload
pause
exit /b

:setup_failed
echo Failed to repair the Python environment.
pause
exit /b 1
