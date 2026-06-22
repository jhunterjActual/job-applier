# Start Script for AI Job Applier Agent
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Starting AI Job Applier Agent...       " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Change directory to backend
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\backend"

# Check if venv exists
if (-not (Test-Path "venv")) {
    Write-Host "Virtual environment 'venv' not found. Starting automatic setup..." -ForegroundColor Yellow
    
    # Check if python is installed
    if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
        Write-Host "Error: Python is not installed or not in your PATH. Please install Python 3.10+." -ForegroundColor Red
        Exit
    }
    
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv venv
    if (-not $?) {
        Write-Host "Failed to create virtual environment." -ForegroundColor Red
        Exit
    }
    
    Write-Host "Activating virtual environment..." -ForegroundColor Yellow
    & ".\venv\Scripts\Activate.ps1"
    
    Write-Host "Installing dependencies from requirements.txt..." -ForegroundColor Yellow
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if (-not $?) {
        Write-Host "Failed to install Python dependencies." -ForegroundColor Red
        Exit
    }
    
    Write-Host "Installing Playwright browsers..." -ForegroundColor Yellow
    playwright install chromium
    if (-not $?) {
        Write-Host "Failed to install Playwright browser." -ForegroundColor Red
        Exit
    }
    
    Write-Host "Setup complete!" -ForegroundColor Green
} else {
    # Activate virtual environment
    Write-Host "Activating virtual environment..." -ForegroundColor Yellow
    & ".\venv\Scripts\Activate.ps1"
}

# Launch Browser in the background after server starts
Start-ThreadJob {
    Start-Sleep -Seconds 3
    Write-Host "Opening Dashboard UI in your browser..." -ForegroundColor Green
    Start-Process "http://127.0.0.1:8000/"
}

# Run FastAPI server
Write-Host "Launching FastAPI Backend..." -ForegroundColor Yellow
Write-Host "Access the Dashboard at http://127.0.0.1:8000/" -ForegroundColor Green
Write-Host "Press Ctrl+C to shut down the agent." -ForegroundColor Magenta
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
