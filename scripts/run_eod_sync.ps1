param(
    [string]$Config = "config/market_data.yaml"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $root ".venv\Scripts\python.exe"
$logs = Join-Path $root "logs\market-data"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python is missing: $python"
}

New-Item -ItemType Directory -Path $logs -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logs "eod_$stamp.log"

$env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"
$env:DYADTETRADE_ENABLE_QMT_SIM_TRADING = "false"
$env:DYADTETRADE_ENABLE_QMT_LIVE_TRADING = "false"
$env:DYADTETRADE_EXECUTION_MODE = "disabled"

Push-Location $root
try {
    & $python -m dyadtetrade.data.cli --config $Config eod-sync *>&1 |
        Tee-Object -FilePath $log
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
