# Start Script for AI Job Applier Agent
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Starting AI Job Applier Agent...       " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ScriptDir "backend"
$VenvDir = Join-Path $BackendDir "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
Set-Location $BackendDir

function Test-PythonExecutable([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        & $Path --version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-BootstrapPython {
    $candidates = @()
    $pathPython = Get-Command "python" -ErrorAction SilentlyContinue
    if ($pathPython) { $candidates += $pathPython.Source }
    $candidates += Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

    foreach ($candidate in $candidates) {
        if (Test-PythonExecutable $candidate) { return $candidate }
    }
    return $null
}

if (-not (Test-PythonExecutable $VenvPython)) {
    Write-Host "The project virtual environment is missing or invalid. Repairing it..." -ForegroundColor Yellow
    $BootstrapPython = Find-BootstrapPython
    if (-not $BootstrapPython) {
        Write-Host "Error: A working Python 3.10+ installation was not found." -ForegroundColor Red
        Write-Host "Install Python from python.org, then run this script again." -ForegroundColor Red
        Exit 1
    }

    & $BootstrapPython -m venv --clear $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }

    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Failed to install Python dependencies." }

    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Failed to install the Playwright browser." }
    Write-Host "Environment repair complete." -ForegroundColor Green
}

Write-Host "Python environment verified." -ForegroundColor Green

# Open the UI only after the backend responds, so a failed launch cannot leave
# a misleading cached dashboard in the browser.
Start-ThreadJob -ArgumentList $Port -ScriptBlock {
    param([int]$Port)
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $version = Invoke-RestMethod "http://127.0.0.1:$Port/api/version" -TimeoutSec 1
            if ($version.build -eq "20260807.2") {
                Start-Process "http://127.0.0.1:$Port/?build=20260807.2"
                return
            }
        } catch { }
    }
    Write-Host "The backend did not become ready; the dashboard was not opened." -ForegroundColor Red
} | Out-Null

Write-Host "Launching FastAPI Backend..." -ForegroundColor Yellow
Write-Host "Access the Dashboard at http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "Press Ctrl+C to shut down the agent." -ForegroundColor Magenta
& $VenvPython -m uvicorn app:app --host 127.0.0.1 --port $Port --reload
