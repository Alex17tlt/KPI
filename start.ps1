# Start the KPI dev server.
# Usage (from this folder):  .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "No virtualenv at .venv. Create it with: py -3.11 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

Write-Host "KPI -> http://127.0.0.1:8000   (Ctrl+C to stop)"
& $python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
