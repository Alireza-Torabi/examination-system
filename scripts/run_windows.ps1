# Quick-start script for Windows (PowerShell)
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1
# Requirements: Python 3.12+ installed and on PATH.

param(
    [string]$PythonExe = "python",
    [string]$VenvPath = ".venv",
    [int]$Port = 5000
)

function Write-Step($msg) {
    Write-Host "==> $msg" -ForegroundColor Cyan
}

Write-Step "Creating virtual environment at $VenvPath"
& $PythonExe -m venv $VenvPath
if ($LASTEXITCODE -ne 0) { throw "Failed to create venv (is Python on PATH?)." }

Write-Step "Activating virtual environment"
$activate = Join-Path $VenvPath "Scripts\Activate.ps1"
if (-not (Test-Path $activate)) { throw "Activate script not found at $activate" }
. $activate

Write-Step "Installing dependencies"
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Step "Initializing database"
python app.py initdb
if ($LASTEXITCODE -ne 0) { throw "initdb failed" }

Write-Step "Starting development server on port $Port"
$env:PORT = $Port
python app.py
