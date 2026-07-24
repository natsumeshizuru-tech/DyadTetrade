$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Start-Process powershell -WindowStyle Hidden -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location -LiteralPath '$root'; .\.venv\Scripts\Activate.ps1; python -m uvicorn dyadtetrade.api.main:app --reload --host 127.0.0.1 --port 8000"
)

Start-Process powershell -WindowStyle Hidden -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location -LiteralPath '$root\frontend'; npm run dev"
)

Write-Host "DyadTetrade backend: http://127.0.0.1:8000/docs"
Write-Host "DyadTetrade frontend: http://127.0.0.1:5173"

