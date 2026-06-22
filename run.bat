@echo off
title AI Job Applier Agent
echo ==========================================
echo    Starting AI Job Applier Agent...
echo ==========================================

cd %~dp0\backend

if not exist venv (
    echo Virtual environment 'venv' not found. Starting automatic setup...
    
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo Error: Python is not installed or not in your PATH. Please install Python 3.10+.
        pause
        exit /b
    )
    
    echo Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo Failed to create virtual environment.
        pause
        exit /b
    )
    
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
    
    echo Installing dependencies from requirements.txt...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo Failed to install Python dependencies.
        pause
        exit /b
    )
    
    echo Installing Playwright browsers...
    playwright install chromium
    if %errorlevel% neq 0 (
        echo Failed to install Playwright browser.
        pause
        exit /b
    )
    
    echo Setup complete!
) else (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

echo Opening Dashboard UI in browser...
start http://127.0.0.1:8000/

echo Launching FastAPI Backend...
echo Access the Dashboard at http://127.0.0.1:8000/
echo Press Ctrl+C to shut down the agent.
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
pause
