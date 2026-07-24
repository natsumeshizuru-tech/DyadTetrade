param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArguments
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".xtdata-venv\Scripts\python.exe"
$Source = Join-Path $ProjectRoot "src"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "XtData Python 3.11 environment is missing: $Python"
}

$env:PYTHONPATH = $Source
$env:DYADTETRADE_XTDATA_IN_PROCESS = "1"
Push-Location $ProjectRoot
try {
    & $Python -m dyadtetrade.data.cli @CliArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
