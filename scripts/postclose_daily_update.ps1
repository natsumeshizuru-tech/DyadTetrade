$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
Set-Location $root
& $python (Join-Path $PSScriptRoot "postclose_daily_update.py")
if ($LASTEXITCODE -ne 0) { throw "post-close daily update failed" }
