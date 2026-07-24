from __future__ import annotations

from pathlib import Path  # noqa: I001

ROOT = Path(__file__).parents[1]


def test_preopen_check_preserves_explicit_qmt_live_release_flag() -> None:
    source = (ROOT / "scripts" / "preopen_check.ps1").read_text(encoding="utf-8")
    assert '$env:DYADTETRADE_EXECUTION_MODE -ne "qmt_live"' in source
    assert '$env:DYADTETRADE_ENABLE_LIVE_TRADING = "false"' in source
    assert "s.installed and s.connected" in source
    assert "not s.live_trading_enabled" not in source


def test_qmt_live_requires_explicit_risk_limits() -> None:
    source = (ROOT / "scripts" / "start_trading_workbench.ps1").read_text(encoding="utf-8")
    for name in (
        "DYADTETRADE_MAX_ORDER_NOTIONAL",
        "DYADTETRADE_MAX_DAILY_NOTIONAL",
        "DYADTETRADE_MAX_SYMBOL_POSITION_NOTIONAL",
        "DYADTETRADE_MAX_ORDERS_PER_MINUTE",
    ):
        assert f'"{name}"' in source
        assert f"$env:{name}" in source or "GetEnvironmentVariable($name)" in source
    live_branch, simulation_branch = source.split("else {", maxsplit=1)
    assert "$defaults" not in live_branch
    assert "$defaults" in simulation_branch


def test_qmt_live_wrapper_loads_local_profile_and_preserves_daily_confirmation() -> None:
    wrapper = (ROOT / "scripts" / "start_qmt_live.ps1").read_text(encoding="utf-8")
    startup = (ROOT / "scripts" / "start_trading_workbench.ps1").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "config\\qmt_live.local.ps1" in wrapper
    assert 'ExecutionMode = "qmt_live"' in wrapper
    assert "LiveConfirmation" in wrapper
    assert "FullMarketConfirmation" in wrapper
    assert 'Read-Host "Type I_CONFIRM_QMT_LIVE' in startup
    assert 'if ($confirmation -cne "I_CONFIRM_QMT_LIVE")' in startup
    assert 'if ($fullMarketConfirmation -cne "I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE")' in startup
    assert "config/qmt_live.local.ps1" in gitignore


def test_trading_startup_reports_progress_and_captures_engine_logs() -> None:
    source = (ROOT / "scripts" / "start_trading_workbench.ps1").read_text(encoding="utf-8")
    assert "account reconciliation can take up to 60 seconds" in source
    assert 'Join-Path $runtime "trading.stdout.log"' in source
    assert 'Join-Path $runtime "trading.stderr.log"' in source


def test_qmt_shortcut_discovery_requires_an_existing_client_executable() -> None:
    source = (ROOT / "scripts" / "start_trading_workbench.ps1").read_text(encoding="utf-8")
    discovery = source.split("$simulationWord =", maxsplit=1)[1].split(
        "$bin = Split-Path", maxsplit=1
    )[0]

    assert "$shell.CreateShortcut($_.FullName)" in discovery
    assert "-not [string]::IsNullOrWhiteSpace($target)" in discovery
    assert "Test-Path -LiteralPath $target -PathType Leaf" in discovery
    assert '[System.IO.Path]::GetFileName($target) -ieq "XtItClient.exe"' in discovery
    assert discovery.index("Test-Path -LiteralPath $target") < discovery.index(
        "Select-Object -First 1"
    )


def test_xtquant_linker_uses_an_explicit_source_and_the_project_venv() -> None:
    source = (ROOT / "scripts" / "link_xtquant.ps1").read_text(encoding="utf-8")

    assert "[Parameter(Mandatory = $true)]" in source
    assert "[string]$SourceSitePackages" in source
    assert 'Join-Path $projectRoot ".venv\\Scripts\\python.exe"' in source
    assert "Resolve-Path -LiteralPath $SourceSitePackages" in source
    assert "xtquant preflight ok:" in source
    assert "[System.IO.File]::ReadAllBytes($pthPath)" in source
    assert "[System.IO.File]::WriteAllBytes($pthPath, $previousPthBytes)" in source


def test_full_check_uses_isolated_runtime_storage() -> None:
    source = (ROOT / "scripts" / "check_all.ps1").read_text(encoding="utf-8")
    migration = source.index('Invoke-Checked "empty database migration"')
    for name in (
        "DYADTETRADE_SQLITE_URL",
        "DYADTETRADE_DUCKDB_PATH",
        "DYADTETRADE_DATA_ROOT",
        "DYADTETRADE_USER_STRATEGIES_PATH",
    ):
        assert source.index(f"$env:{name}") < migration
    assert "data\\runtime-checks" in source


def test_qmt_sim_startup_keeps_restricted_risk_states_available_for_diagnostics() -> None:
    startup = (ROOT / "scripts" / "start_trading_workbench.ps1").read_text(encoding="utf-8")
    preopen = (ROOT / "scripts" / "preopen_check.ps1").read_text(encoding="utf-8")
    for state in ("NORMAL", "REDUCE_ONLY", "CANCEL_ONLY", "HALTED", "RECOVERY"):
        assert f'"{state}"' in startup
        assert f'"{state}"' in preopen
    assert 'if ($ExecutionMode -eq "qmt_live"' in startup
    assert 'if ($env:DYADTETRADE_EXECUTION_MODE -eq "qmt_live"' in preopen
    assert "diagnostics remain available but new orders are blocked" in startup
