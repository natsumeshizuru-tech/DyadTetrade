param(
    [string]$TaskName = "DyadTetrade Market Data EOD",
    [string]$RunTime = "16:10"
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runner = Join-Path $PSScriptRoot "run_eod_sync.ps1"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "EOD runner is missing: $runner"
}

try {
    $at = [datetime]::ParseExact($RunTime, "HH:mm", $null)
}
catch {
    throw "RunTime must use HH:mm"
}

$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $arguments `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -WeeksInterval 1 `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $at
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 6 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Read-only miniQMT market-data sync into sharded SQLite" `
    -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State,Description
