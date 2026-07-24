param(
    [switch]$SkipMiniQmt
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$ruff = Join-Path $root ".venv\Scripts\ruff.exe"
$mypy = Join-Path $root ".venv\Scripts\mypy.exe"
$pytest = Join-Path $root ".venv\Scripts\pytest.exe"
$alembic = Join-Path $root ".venv\Scripts\alembic.exe"
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$checkId = "check-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $PID
$checkRoot = Join-Path $root "data\runtime-checks\$checkId"
$checkDatabase = Join-Path $checkRoot "dyadtetrade-test.db"
$checkDatabaseUrlPath = $checkDatabase.Replace([char]92, [char]47)
New-Item -ItemType Directory -Path $checkRoot -Force | Out-Null
$env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
$env:DYADTETRADE_EXECUTION_MODE = "disabled"
$env:DYADTETRADE_SQLITE_URL = "sqlite+aiosqlite:///$checkDatabaseUrlPath"
$env:DYADTETRADE_DUCKDB_PATH = Join-Path $checkRoot "research-test.duckdb"
$env:DYADTETRADE_DATA_ROOT = Join-Path $checkRoot "data"
$env:DYADTETRADE_USER_STRATEGIES_PATH = Join-Path $checkRoot "user-strategies"
$env:PYTHONUTF8 = "1"

function Invoke-Checked {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "[check] $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

function Stop-ProcessTree {
    param([int]$Id)
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$Id" -ErrorAction SilentlyContinue)
    foreach ($child in $children) { Stop-ProcessTree -Id $child.ProcessId }
    if (Get-Process -Id $Id -ErrorAction SilentlyContinue) { Stop-Process -Id $Id -Force }
}

Set-Location $root
Invoke-Checked "environment" { & $python scripts\check_environment.py }
Invoke-Checked "pip check" { & $python -m pip check }
Invoke-Checked "empty database migration" { & $alembic upgrade head }
Invoke-Checked "database migration diff" { & $alembic check }
Invoke-Checked "ruff" { & $ruff check . }
Invoke-Checked "ruff format" { & $ruff format --check . }
Invoke-Checked "strict mypy" { & $mypy }
Invoke-Checked "trading dependency boundary" { & $python scripts\check_trading_imports.py }
Invoke-Checked "OpenAPI TypeScript generation" { & $python scripts\generate_openapi_types.py }
Invoke-Checked "pytest and coverage" {
    & $pytest --cov=dyadtetrade --cov-report=term --cov-report=xml:reports\coverage.xml
}

Push-Location frontend
try {
    Invoke-Checked "frontend tests" { & $npm test }
    Invoke-Checked "frontend eslint" { & $npm run lint }
    Invoke-Checked "frontend production build" { & $npm run build }
}
finally {
    Pop-Location
}

Invoke-Checked "live-trading fail-closed" {
    & $python -c "from dyadtetrade.core.config import get_settings; assert get_settings().enable_live_trading is False"
}

if (-not $SkipMiniQmt) {
    Invoke-Checked "miniQMT read-only data status" {
        & $python -m dyadtetrade.data.cli doctor
    }
}

$server = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "dyadtetrade.api.main:app", "--host", "127.0.0.1", "--port", "8765" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/v1/ready" -TimeoutSec 2
            if ($response.status -eq "ready" -and -not $response.live_trading_enabled) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) { throw "backend readiness smoke failed" }
    Write-Host "[check] backend and API smoke passed" -ForegroundColor Green
}
finally {
    $listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if ($process -and $process.CommandLine -like "*dyadtetrade.api.main:app*8765*") {
            Stop-ProcessTree -Id $listener.OwningProcess
        }
    }
    Stop-ProcessTree -Id $server.Id
}

Write-Host "All DyadTetrade checks passed; live trading remained disabled." -ForegroundColor Green
