param(
    [ValidateSet("qmt_sim", "qmt_live")]
    [string]$ExecutionMode = "qmt_sim",
    [string]$LiveConfirmation = "",
    [string]$FullMarketConfirmation = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$runtime = Join-Path $root "logs\runtime"
$env:DYADTETRADE_TRADING_ENGINE_TOKEN = [Guid]::NewGuid().ToString("N")
$env:DYADTETRADE_EXECUTION_MODE = $ExecutionMode
$env:DYADTETRADE_QMT_ACCOUNT_TYPE = "STOCK"
if ($ExecutionMode -eq "qmt_live") {
    Write-Host "[qmt_live] Validating the explicit account, allowlist, and risk limits..." -ForegroundColor Cyan
    $dynamicUniverse = $env:DYADTETRADE_ENABLE_DYNAMIC_EXECUTION_UNIVERSE -eq "true"
    $required = @(
        "DYADTETRADE_QMT_ACCOUNT_ID",
        "DYADTETRADE_QMT_USERDATA_PATH",
        "DYADTETRADE_QMT_LIVE_ACCOUNT_WHITELIST",
        "DYADTETRADE_MAX_ORDER_NOTIONAL",
        "DYADTETRADE_MAX_DAILY_NOTIONAL",
        "DYADTETRADE_MAX_SYMBOL_POSITION_NOTIONAL",
        "DYADTETRADE_MAX_ORDERS_PER_MINUTE",
        "DYADTETRADE_MAX_TOTAL_POSITION_NOTIONAL",
        "DYADTETRADE_MAX_TOTAL_POSITION_RATIO",
        "DYADTETRADE_MAX_DAILY_ORDERS",
        "DYADTETRADE_MAX_STRATEGY_DAILY_NOTIONAL"
    )
    if ($dynamicUniverse) {
        $required += @(
            "DYADTETRADE_EXECUTION_UNIVERSE_RELEASE_ID",
            "DYADTETRADE_ENABLE_FULL_MARKET_LIVE_TRADING"
        )
    }
    else { $required += "DYADTETRADE_ALLOWED_SYMBOLS" }
    foreach ($name in $required) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            throw "$name must be set explicitly before qmt_live startup"
        }
    }
    foreach ($name in @(
        "DYADTETRADE_MAX_ORDER_NOTIONAL",
        "DYADTETRADE_MAX_DAILY_NOTIONAL",
        "DYADTETRADE_MAX_SYMBOL_POSITION_NOTIONAL",
        "DYADTETRADE_MAX_TOTAL_POSITION_NOTIONAL",
        "DYADTETRADE_MAX_STRATEGY_DAILY_NOTIONAL"
    )) {
        try { $limit = [decimal]([Environment]::GetEnvironmentVariable($name)) }
        catch { throw "$name must be a positive number" }
        if ($limit -le 0) { throw "$name must be a positive number" }
    }
    try { $ordersPerMinute = [int]$env:DYADTETRADE_MAX_ORDERS_PER_MINUTE }
    catch { throw "DYADTETRADE_MAX_ORDERS_PER_MINUTE must be a positive integer" }
    if ($ordersPerMinute -le 0) { throw "DYADTETRADE_MAX_ORDERS_PER_MINUTE must be a positive integer" }
    try { $dailyOrders = [int]$env:DYADTETRADE_MAX_DAILY_ORDERS }
    catch { throw "DYADTETRADE_MAX_DAILY_ORDERS must be a positive integer" }
    if ($dailyOrders -le 0) { throw "DYADTETRADE_MAX_DAILY_ORDERS must be a positive integer" }
    try { $positionRatio = [decimal]$env:DYADTETRADE_MAX_TOTAL_POSITION_RATIO }
    catch { throw "DYADTETRADE_MAX_TOTAL_POSITION_RATIO must be in (0, 1]" }
    if ($positionRatio -le 0 -or $positionRatio -gt 1) { throw "DYADTETRADE_MAX_TOTAL_POSITION_RATIO must be in (0, 1]" }
    if ([decimal]$env:DYADTETRADE_MAX_ORDER_NOTIONAL -gt [decimal]$env:DYADTETRADE_MAX_DAILY_NOTIONAL) {
        throw "DYADTETRADE_MAX_ORDER_NOTIONAL cannot exceed DYADTETRADE_MAX_DAILY_NOTIONAL"
    }
    $confirmation = $LiveConfirmation
    if ([string]::IsNullOrWhiteSpace($confirmation)) {
        $confirmation = Read-Host "Type I_CONFIRM_QMT_LIVE to release qmt_live for today"
    }
    if ($confirmation -cne "I_CONFIRM_QMT_LIVE") { throw "qmt_live confirmation failed" }
    $env:DYADTETRADE_ENABLE_LIVE_TRADING = "true"
    $env:DYADTETRADE_ENABLE_QMT_LIVE_TRADING = "true"
    $env:DYADTETRADE_ENABLE_QMT_SIM_TRADING = "false"
    $env:DYADTETRADE_QMT_LIVE_CONFIRMATION = $confirmation
    $env:DYADTETRADE_QMT_LIVE_RELEASE_DATE = (Get-Date).ToString("yyyy-MM-dd")
    Write-Host "[qmt_live] Daily release accepted for account $env:DYADTETRADE_QMT_ACCOUNT_ID." -ForegroundColor Green
    if ($dynamicUniverse) {
        $fullMarketConfirmation = $FullMarketConfirmation
        if ([string]::IsNullOrWhiteSpace($fullMarketConfirmation)) {
            $fullMarketConfirmation = Read-Host "Type I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE to release today's dynamic universe"
        }
        if ($fullMarketConfirmation -cne "I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE") {
            throw "full-market live universe confirmation failed"
        }
        $env:DYADTETRADE_FULL_MARKET_LIVE_CONFIRMATION = $fullMarketConfirmation
        $env:DYADTETRADE_FULL_MARKET_LIVE_RELEASE_DATE = (Get-Date).ToString("yyyy-MM-dd")
    }
    if ($env:DYADTETRADE_ENABLE_AUTOMATED_STRATEGY_EXECUTION -eq "true") {
        $automatedConfirmation = Read-Host "Type I_CONFIRM_AUTOMATED_LIVE_EXECUTION to release automated strategy execution for today"
        if ($automatedConfirmation -cne "I_CONFIRM_AUTOMATED_LIVE_EXECUTION") {
            throw "automated live execution confirmation failed"
        }
        $env:DYADTETRADE_AUTOMATED_LIVE_CONFIRMATION = $automatedConfirmation
        $env:DYADTETRADE_AUTOMATED_LIVE_RELEASE_DATE = (Get-Date).ToString("yyyy-MM-dd")
    }
}
else {
    $defaults = @{
        DYADTETRADE_MAX_ORDER_NOTIONAL = "10000"
        DYADTETRADE_MAX_DAILY_NOTIONAL = "50000"
        DYADTETRADE_MAX_SYMBOL_POSITION_NOTIONAL = "20000"
        DYADTETRADE_MAX_ORDERS_PER_MINUTE = "5"
        DYADTETRADE_MAX_TOTAL_POSITION_NOTIONAL = "100000"
        DYADTETRADE_MAX_TOTAL_POSITION_RATIO = "0.80"
        DYADTETRADE_MAX_DAILY_ORDERS = "100"
        DYADTETRADE_MAX_STRATEGY_DAILY_NOTIONAL = "20000"
    }
    foreach ($name in $defaults.Keys) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
            [Environment]::SetEnvironmentVariable($name, $defaults[$name])
        }
    }
    $env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
    $env:DYADTETRADE_ENABLE_QMT_LIVE_TRADING = "false"
    $env:DYADTETRADE_ENABLE_QMT_SIM_TRADING = "true"
    $env:DYADTETRADE_QMT_LIVE_CONFIRMATION = ""
    $env:DYADTETRADE_QMT_LIVE_RELEASE_DATE = ""
    $env:DYADTETRADE_FULL_MARKET_LIVE_CONFIRMATION = ""
    $env:DYADTETRADE_FULL_MARKET_LIVE_RELEASE_DATE = ""
    $env:DYADTETRADE_AUTOMATED_LIVE_CONFIRMATION = ""
    $env:DYADTETRADE_AUTOMATED_LIVE_RELEASE_DATE = ""
    if ([string]::IsNullOrWhiteSpace($env:DYADTETRADE_ALLOWED_SYMBOLS)) {
        $env:DYADTETRADE_ALLOWED_SYMBOLS = "510300.SH"
    }
    if ($env:DYADTETRADE_ENABLE_DYNAMIC_EXECUTION_UNIVERSE -eq "true" -and [string]::IsNullOrWhiteSpace($env:DYADTETRADE_EXECUTION_UNIVERSE_RELEASE_ID)) {
        $releaseId = (& $python (Join-Path $PSScriptRoot "get_active_execution_universe.py") --mode qmt_sim 2>$null).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($releaseId)) {
            & $python (Join-Path $PSScriptRoot "build_execution_universe.py") --mode qmt_sim --review --activate --actor preopen-automation --confirmation I_CONFIRM_QMT_SIM_UNIVERSE
            if ($LASTEXITCODE -ne 0) { throw "automatic qmt_sim universe preparation failed" }
            $releaseId = (& $python (Join-Path $PSScriptRoot "get_active_execution_universe.py") --mode qmt_sim).Trim()
        }
        if ([string]::IsNullOrWhiteSpace($releaseId)) {
            throw "no active qmt_sim execution universe is available for today"
        }
        $env:DYADTETRADE_EXECUTION_UNIVERSE_RELEASE_ID = $releaseId
    }
}

if ($ExecutionMode -eq "qmt_sim" -and ([string]::IsNullOrWhiteSpace($env:DYADTETRADE_QMT_USERDATA_PATH) -or [string]::IsNullOrWhiteSpace($env:DYADTETRADE_QMT_ACCOUNT_ID))) {
    $shell = New-Object -ComObject WScript.Shell
    $simulationWord = [string]([char]0x6A21) + [string]([char]0x62DF)
    $shortcut = Get-ChildItem -Path "$env:USERPROFILE\Desktop", "$env:APPDATA\Microsoft\Windows\Start Menu", "$env:ProgramData\Microsoft\Windows\Start Menu" -Recurse -Filter *.lnk -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -match "QMT" -and $_.BaseName.Contains($simulationWord) -and $_.BaseName -notmatch "uninst" } |
        ForEach-Object {
            $candidateShortcut = $shell.CreateShortcut($_.FullName)
            $target = $candidateShortcut.TargetPath
            if ((-not [string]::IsNullOrWhiteSpace($target)) -and (Test-Path -LiteralPath $target -PathType Leaf) -and [System.IO.Path]::GetFileName($target) -ieq "XtItClient.exe") {
                $candidateShortcut
            }
        } | Select-Object -First 1
    if (-not $shortcut) { throw "simulation QMT shortcut targeting an existing XtItClient.exe was not found" }
    $bin = Split-Path $shortcut.TargetPath -Parent
    $roots = @($bin, (Split-Path $bin -Parent), (Split-Path (Split-Path $bin -Parent) -Parent)) | Select-Object -Unique
    $userdata = $null
    foreach ($candidateRoot in $roots) {
        $candidate = Join-Path $candidateRoot "userdata_mini"
        if (Test-Path -LiteralPath $candidate) { $userdata = $candidate; break }
    }
    if (-not $userdata) { throw "simulation userdata_mini was not found" }
    $users = @(Get-ChildItem -LiteralPath (Join-Path $userdata "users") -Directory)
    if ($users.Count -ne 1) { throw "exactly one simulation account directory is required" }
    $env:DYADTETRADE_QMT_USERDATA_PATH = $userdata
    $env:DYADTETRADE_QMT_ACCOUNT_ID = $users[0].Name
}
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

$apiPid = Join-Path $runtime "api.pid"
$webPid = Join-Path $runtime "web.pid"
$tradingPid = Join-Path $runtime "trading.pid"
if ((Test-Path $apiPid) -or (Test-Path $webPid) -or (Test-Path $tradingPid)) { throw "runtime PID files already exist; stop the workbench first" }
foreach ($port in @(8011, 8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "port $port is already in use; refusing to attach to an unowned process"
    }
}

$tradingStdout = Join-Path $runtime "trading.stdout.log"
$tradingStderr = Join-Path $runtime "trading.stderr.log"
Write-Host "[startup] Starting the QMT trading engine; account reconciliation can take up to 60 seconds..." -ForegroundColor Cyan
$trading = Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "dyadtetrade.trading.app:app", "--app-dir", $root, "--host", "127.0.0.1", "--port", "8011" -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $tradingStdout -RedirectStandardError $tradingStderr -PassThru
Set-Content -LiteralPath $tradingPid -Value $trading.Id
$engineHealth = $null
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    try {
        $engineHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8011/health" -TimeoutSec 2
        if ($engineHealth.status -eq "ok") { break }
    }
    catch { Start-Sleep -Milliseconds 500 }
}
if (-not $engineHealth -or $engineHealth.status -ne "ok") {
    $engineError = if (Test-Path -LiteralPath $tradingStderr) {
        (Get-Content -LiteralPath $tradingStderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    }
    else { "" }
    & (Join-Path $root "scripts\stop_trading_workbench.ps1")
    throw ("trading engine startup timeout" + $(if ($engineError) { [Environment]::NewLine + $engineError } else { "" }))
}
Write-Host "[startup] Trading engine health endpoint is ready; checking the QMT account..." -ForegroundColor Cyan
$engineListeners = @(Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction Stop)
if ($engineListeners.Count -ne 1) {
    & (Join-Path $root "scripts\stop_trading_workbench.ps1")
    throw "the trading engine must be the only listener on port 8011"
}
$engineProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($engineListeners[0].OwningProcess)"
if (-not $engineProcess -or $engineProcess.CommandLine -notlike "*dyadtetrade.trading.app:app*") {
    & (Join-Path $root "scripts\stop_trading_workbench.ps1")
    throw "port 8011 listener is not the launched DyadTetrade trading engine"
}
Set-Content -LiteralPath $tradingPid -Value $engineListeners[0].OwningProcess
$engineHeaders = @{ "X-Trading-Engine-Token" = $env:DYADTETRADE_TRADING_ENGINE_TOKEN }
$engineStatus = Invoke-RestMethod -Uri "http://127.0.0.1:8011/internal/v1/status" -Headers $engineHeaders -TimeoutSec 5
$safeRiskStates = @("NORMAL", "REDUCE_ONLY", "CANCEL_ONLY", "HALTED", "RECOVERY")
if (-not $engineStatus.connected -or -not $engineStatus.subscribed -or -not $engineStatus.account_healthy -or $safeRiskStates -notcontains $engineStatus.risk_state.state) {
    & (Join-Path $root "scripts\stop_trading_workbench.ps1")
    throw "trading engine did not complete QMT account reconciliation"
}
if ($ExecutionMode -eq "qmt_live" -and $engineStatus.risk_state.state -ne "NORMAL") {
    & (Join-Path $root "scripts\stop_trading_workbench.ps1")
    throw "qmt_live requires NORMAL risk state"
}
Write-Host "[startup] QMT account is connected, subscribed, and reconciled (risk=$($engineStatus.risk_state.state))." -ForegroundColor Green
if ($engineStatus.risk_state.state -ne "NORMAL") {
    Write-Warning "Trading workbench is starting in $($engineStatus.risk_state.state); diagnostics remain available but new orders are blocked by the risk state."
}
Write-Host "[startup] Starting the API and frontend..." -ForegroundColor Cyan
$api = Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "dyadtetrade.api.main:app", "--app-dir", $root, "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory $root -WindowStyle Hidden -PassThru
$web = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173" -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $apiPid -Value $api.Id
Set-Content -LiteralPath $webPid -Value $web.Id
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/ready" -TimeoutSec 2
            $front = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -UseBasicParsing -TimeoutSec 2
            if ($status.status -eq "ready" -and $front.StatusCode -eq 200) { $ready = $true; break }
        }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "workbench startup timeout" }
    $apiListeners = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop)
    $webListeners = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction Stop)
    if ($apiListeners.Count -ne 1 -or $webListeners.Count -ne 1) { throw "API and frontend require exactly one listener each" }
    Set-Content -LiteralPath $apiPid -Value $apiListeners[0].OwningProcess
    Set-Content -LiteralPath $webPid -Value $webListeners[0].OwningProcess
    & (Join-Path $root "scripts\preopen_check.ps1")
    Write-Host "DyadTetrade $ExecutionMode is ready at http://127.0.0.1:5173" -ForegroundColor Green
}
catch {
    & (Join-Path $root "scripts\stop_trading_workbench.ps1")
    throw
}
