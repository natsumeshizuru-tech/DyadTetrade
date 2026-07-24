param([switch]$SkipWeb)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$alembic = Join-Path $root ".venv\Scripts\alembic.exe"
if ($env:DYADTETRADE_EXECUTION_MODE -ne "qmt_live") {
    $env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
}
$env:PYTHONUTF8 = "1"
Set-Location $root

Write-Host "[preopen] miniQMT process" -ForegroundColor Cyan
if (-not (Get-Process -Name "XtMiniQmt" -ErrorAction SilentlyContinue)) { throw "miniQMT is not running" }
$tradingPidPath = Join-Path $root "logs\runtime\trading.pid"
if (-not (Test-Path -LiteralPath $tradingPidPath)) { throw "trading engine PID is missing" }
$tradingPid = [int](Get-Content -LiteralPath $tradingPidPath)
$tradingProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$tradingPid"
if (-not $tradingProcess) { throw "trading engine process is not running" }
if ($tradingProcess.CommandLine -match "--reload" -or $tradingProcess.CommandLine -match "--workers\s+[2-9]") { throw "trading engine must use one worker without reload" }
$listeners = @(Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -ne 1 -or $listeners[0].OwningProcess -ne $tradingPid) { throw "exactly one trading engine listener is required" }
$engineToken = $env:DYADTETRADE_TRADING_ENGINE_TOKEN
if (-not [string]::IsNullOrWhiteSpace($engineToken)) {
    $engineDirect = Invoke-RestMethod -Uri "http://127.0.0.1:8011/internal/v1/status" -Headers @{ "X-Trading-Engine-Token" = $engineToken } -TimeoutSec 5
    if (-not $engineDirect.engine_online -or $engineDirect.engine_pid -ne $tradingPid) { throw "authenticated trading engine identity check failed" }
}

Write-Host "[preopen] XtQuant, XtData and migrations" -ForegroundColor Cyan
& $python -c "from dyadtetrade.integrations.xtquant import get_xtquant_status; s=get_xtquant_status(); assert s.installed and s.connected; print({'connected':s.connected,'live':s.live_trading_enabled,'sectors':s.sector_count})"
if ($LASTEXITCODE -ne 0) { throw "XtData check failed" }
& $alembic upgrade head
& $alembic check
if ($LASTEXITCODE -ne 0) { throw "database migration check failed" }
& $python (Join-Path $PSScriptRoot "sync_trading_calendar.py")
if ($LASTEXITCODE -ne 0) { throw "trading calendar synchronization failed" }

Write-Host "[preopen] execution safety" -ForegroundColor Cyan
& $python -c "from dyadtetrade.core.config import get_settings; s=get_settings(); s.validate_execution_safety(); print({'mode':s.execution_mode.value,'live':s.enable_live_trading,'allowed_symbols':len(s.allowed_symbols)})"
if ($LASTEXITCODE -ne 0) { throw "execution safety configuration failed" }

Write-Host "[preopen] latest daily data" -ForegroundColor Cyan
& $python (Join-Path $PSScriptRoot "preopen_data_check.py")
if ($LASTEXITCODE -ne 0) { throw "daily data is not updated to the latest trading day" }

if (-not $SkipWeb) {
    Write-Host "[preopen] backend, frontend and account subscription" -ForegroundColor Cyan
    $ready = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/ready" -TimeoutSec 5
    if ($ready.status -ne "ready") { throw "backend is not ready" }
    $execution = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/execution/status" -TimeoutSec 5
    Write-Host "[preopen] engine=$($execution.engine_online) connected=$($execution.connected) subscribed=$($execution.subscribed) account_healthy=$($execution.account_healthy) risk=$($execution.risk_state.state) error=$($execution.last_error)"
    if (-not $execution.engine_online -or -not $execution.connected -or -not $execution.subscribed -or -not $execution.account_healthy) { throw "QMT account is not safely subscribed" }
    if ($execution.engine_pid -ne $tradingPid) { throw "API proxy and trading engine PID do not match" }
    $safeRiskStates = @("NORMAL", "REDUCE_ONLY", "CANCEL_ONLY", "HALTED", "RECOVERY")
    if ($safeRiskStates -notcontains $execution.risk_state.state) { throw "unknown risk state" }
    if ($env:DYADTETRADE_EXECUTION_MODE -eq "qmt_live" -and $execution.risk_state.state -ne "NORMAL") {
        throw "qmt_live pre-open check requires NORMAL risk state"
    }
    if ($execution.risk_state.state -ne "NORMAL") {
        Write-Warning "Risk state is $($execution.risk_state.state); diagnostics are available but new orders remain blocked."
    }
    if ($env:DYADTETRADE_ENABLE_DYNAMIC_EXECUTION_UNIVERSE -eq "true") {
        if (-not $execution.execution_universe -or $execution.execution_universe.status -ne "active") {
            throw "the daily dynamic execution universe is not active"
        }
        if ($execution.execution_universe.release_id -ne $env:DYADTETRADE_EXECUTION_UNIVERSE_RELEASE_ID) {
            throw "the loaded execution universe does not match the configured release"
        }
        if ($execution.execution_universe.symbol_count -le 0) { throw "execution universe is empty" }
        Write-Host "[preopen] universe=$($execution.execution_universe.release_id) symbols=$($execution.execution_universe.symbol_count) hash=$($execution.execution_universe.snapshot_hash.Substring(0, 12))"
    }
    if ($execution.execution_mode -eq "qmt_sim" -and $ready.live_trading_enabled) { throw "qmt_sim cannot enable live trading" }
    if ($execution.execution_mode -eq "qmt_live") {
        $release = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/execution/release-gate" -TimeoutSec 5
        if (-not $release.ready) { throw "qmt_live release gate is not ready" }
    }
    $coverage = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/market/summary" -TimeoutSec 5
    Write-Host "[preopen] daily coverage $($coverage.latest_trade_date_covered_count)/$($coverage.instrument_count); missing=$($coverage.missing_instrument_count); failed=$($coverage.failed_download_item_count)"
    $web = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 5 -UseBasicParsing
    if ($web.StatusCode -ne 200) { throw "frontend is unavailable" }
    & $python -c "import json; from websockets.sync.client import connect; w=connect('ws://127.0.0.1:8000/api/v1/ws/monitor'); d=json.loads(w.recv(timeout=5)); print({'type':d['type'],'live':d['live_trading_enabled'],'xtdata':d['miniqmt_connected'],'execution':d['execution']['execution_mode'],'subscribed':d['execution']['subscribed']}); w.close()"
    if ($LASTEXITCODE -ne 0) { throw "monitor WebSocket check failed" }
}

$mode = $env:DYADTETRADE_EXECUTION_MODE
Write-Host "Pre-open safety checks passed for $mode. No order was sent." -ForegroundColor Green
