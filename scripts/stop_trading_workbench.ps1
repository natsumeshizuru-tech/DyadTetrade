$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root "logs\runtime"
$resolvedRoot = [IO.Path]::GetFullPath($root)
$resolvedRuntime = [IO.Path]::GetFullPath($runtime)
if (-not $resolvedRuntime.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw "runtime path escaped project root" }
function Stop-ProcessTree {
    param([int]$Id)
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$Id" -ErrorAction SilentlyContinue)
    foreach ($child in $children) { Stop-ProcessTree -Id $child.ProcessId }
    if (Get-Process -Id $Id -ErrorAction SilentlyContinue) { Stop-Process -Id $Id -Force }
}
foreach ($name in @("api", "web", "trading")) {
    $path = Join-Path $runtime "$name.pid"
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $processId = [int](Get-Content -LiteralPath $path -Raw)
    Stop-ProcessTree -Id $processId
    Remove-Item -LiteralPath $path -Force
}
# Launchers can exit before their Python/Node child binds the port.  Clean up
# only listeners whose command line identifies a DyadTetrade component.
$components = @(
    @{ Port = 8011; Marker = "dyadtetrade.trading.app:app" },
    @{ Port = 8000; Marker = "dyadtetrade.api.main:app" },
    @{ Port = 5173; Marker = $resolvedRoot }
)
foreach ($component in $components) {
    $listeners = @(Get-NetTCPConnection -LocalPort $component.Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if ($process -and $process.CommandLine -like "*$($component.Marker)*") {
            Stop-ProcessTree -Id $listener.OwningProcess
        }
    }
}
Write-Host "DyadTetrade workbench stopped." -ForegroundColor Green
