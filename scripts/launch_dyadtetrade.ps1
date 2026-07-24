param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend"
$runtime = Join-Path $root "logs\runtime"
$url = "http://127.0.0.1:5173"

function Test-Backend {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 2
        return $response.status -eq "ok" -and $response.service -eq "DyadTetrade"
    }
    catch { return $false }
}

function Test-Frontend {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content -match "<title>DyadTetrade</title>"
    }
    catch { return $false }
}

function Show-LaunchError {
    param([string]$Message)
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            $Message,
            "DyadTetrade startup failed",
            [System.Windows.MessageBoxButton]::OK,
            [System.Windows.MessageBoxImage]::Error
        ) | Out-Null
    }
    catch { }
}

try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python runtime is missing: $python"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontend "package.json"))) {
        throw "Frontend project is missing: $frontend"
    }

    if ((Test-Backend) -and (Test-Frontend)) {
        if (-not $NoBrowser) { Start-Process $url }
        exit 0
    }

    foreach ($port in @(8000, 5173)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
            throw "Port $port is already in use by another or partially started process."
        }
    }

    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    foreach ($name in @("api", "web")) {
        $pidFile = Join-Path $runtime "$name.pid"
        if (-not (Test-Path -LiteralPath $pidFile)) { continue }
        $recordedPid = [int](Get-Content -LiteralPath $pidFile -Raw)
        if (Get-Process -Id $recordedPid -ErrorAction SilentlyContinue) {
            throw "A recorded DyadTetrade $name process is still running (PID $recordedPid)."
        }
        Remove-Item -LiteralPath $pidFile -Force
    }

    $env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
    $env:DYADTETRADE_EXECUTION_MODE = "disabled"
    $env:DYADTETRADE_ENABLE_QMT_SIM_TRADING = "false"
    $env:DYADTETRADE_ENABLE_QMT_LIVE_TRADING = "false"

    $api = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "dyadtetrade.api.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtime "api.stdout.log") `
        -RedirectStandardError (Join-Path $runtime "api.stderr.log") `
        -PassThru
    Set-Content -LiteralPath (Join-Path $runtime "api.pid") -Value $api.Id

    $web = Start-Process -FilePath "npm.cmd" `
        -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173") `
        -WorkingDirectory $frontend `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtime "web.stdout.log") `
        -RedirectStandardError (Join-Path $runtime "web.stderr.log") `
        -PassThru
    Set-Content -LiteralPath (Join-Path $runtime "web.pid") -Value $web.Id

    $ready = $false
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        if ((Test-Backend) -and (Test-Frontend)) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "DyadTetrade did not become ready within 45 seconds." }

    $apiListener = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop)
    $webListener = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction Stop)
    if ($apiListener.Count -ne 1 -or $webListener.Count -ne 1) {
        throw "DyadTetrade requires one backend and one frontend listener."
    }
    Set-Content -LiteralPath (Join-Path $runtime "api.pid") -Value $apiListener[0].OwningProcess
    Set-Content -LiteralPath (Join-Path $runtime "web.pid") -Value $webListener[0].OwningProcess

    if (-not $NoBrowser) { Start-Process $url }
}
catch {
    try { & (Join-Path $PSScriptRoot "stop_trading_workbench.ps1") | Out-Null } catch { }
    Show-LaunchError $_.Exception.Message
    exit 1
}
