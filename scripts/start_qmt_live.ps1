param(
    [string]$Confirmation = "",
    [string]$FullMarketConfirmation = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$profile = Join-Path $root "config\qmt_live.local.ps1"

if (-not (Test-Path -LiteralPath $profile)) {
    throw "Local qmt_live profile is missing: $profile"
}

. $profile

$parameters = @{ ExecutionMode = "qmt_live" }
if (-not [string]::IsNullOrWhiteSpace($Confirmation)) {
    $parameters.LiveConfirmation = $Confirmation
}
if (-not [string]::IsNullOrWhiteSpace($FullMarketConfirmation)) {
    $parameters.FullMarketConfirmation = $FullMarketConfirmation
}

& (Join-Path $PSScriptRoot "start_trading_workbench.ps1") @parameters
