param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SourceSitePackages
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$sitePackages = Join-Path $projectRoot ".venv\Lib\site-packages"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pthPath = Join-Path $sitePackages "xtquant-local.pth"

if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
    throw "项目虚拟环境不存在，请先创建 $projectRoot\.venv"
}
if (-not (Test-Path -LiteralPath $SourceSitePackages -PathType Container)) {
    throw "XtQuant site-packages 目录不存在：$SourceSitePackages"
}

$resolvedSourceSitePackages = (Resolve-Path -LiteralPath $SourceSitePackages).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedSourceSitePackages "xtquant") -PathType Container)) {
    throw "未找到本机 XtQuant：$resolvedSourceSitePackages\xtquant"
}

& $python -c "import sys; sys.path.insert(0, sys.argv[1]); from xtquant import xtdata; print('xtquant preflight ok:', xtdata.__file__)" $resolvedSourceSitePackages
if ($LASTEXITCODE -ne 0) {
    throw "指定的 XtQuant 无法由项目 .venv 导入，未修改 $pthPath"
}

$hadPreviousPth = Test-Path -LiteralPath $pthPath -PathType Leaf
$previousPthBytes = if ($hadPreviousPth) { [System.IO.File]::ReadAllBytes($pthPath) } else { $null }
try {
    Set-Content -LiteralPath $pthPath -Value $resolvedSourceSitePackages -Encoding Default
    & $python -c "from xtquant import xtdata; print('xtquant import ok:', xtdata.__file__)"
    if ($LASTEXITCODE -ne 0) { throw "项目 .venv 无法通过 $pthPath 导入 XtQuant" }
}
catch {
    if ($hadPreviousPth) {
        [System.IO.File]::WriteAllBytes($pthPath, $previousPthBytes)
    }
    else {
        Remove-Item -LiteralPath $pthPath -Force -ErrorAction SilentlyContinue
    }
    throw
}

Write-Host "XtQuant 已链接到 DyadTetrade 虚拟环境：$pthPath"
