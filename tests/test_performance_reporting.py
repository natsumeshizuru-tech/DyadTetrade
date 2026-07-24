from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from dyadtetrade.domain import Direction, Trade
from dyadtetrade.modules.backtest import BacktestConfig, BacktestEngine
from dyadtetrade.modules.performance import analyze_performance
from dyadtetrade.modules.reporting import generate_backtest_report
from dyadtetrade.modules.reporting.reporter import _equity_svg
from tests.test_backtest_engine import sample_frame


def test_performance_metrics_are_numerically_reconciled() -> None:
    equity = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=5, freq="B"),
            "total_assets": [100.0, 110.0, 105.0, 120.0, 108.0],
        }
    )
    benchmark = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=pd.to_datetime(equity["date"]))
    result = analyze_performance(
        equity,
        benchmark=benchmark,
        trade_pnls=pd.Series([10.0, -5.0, 20.0]),
        turnover_notional=200.0,
        trade_count=3,
        position_values=pd.Series({"A": 60.0, "B": 40.0}),
    )
    assert result.metrics["total_return"] == pytest.approx(0.08)
    assert result.metrics["max_drawdown"] == pytest.approx(-0.10)
    assert result.metrics["win_rate"] == pytest.approx(2 / 3)
    assert result.metrics["profit_loss_ratio"] == pytest.approx(3.0)
    assert result.metrics["profit_factor"] == pytest.approx(6.0)
    assert result.metrics["trade_count"] == 3
    assert result.metrics["holding_concentration"] == pytest.approx(0.52)
    assert len(result.monthly_returns) == 1
    assert {"net_value", "drawdown", "relative_net_value"}.issubset(result.equity)


def test_report_generates_all_artifacts(tmp_path: Path) -> None:
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        initial_cash=Decimal("100000"),
    )
    backtest = BacktestEngine().run(config, sample_frame())
    performance = analyze_performance(backtest.equity_frame())
    artifacts = generate_backtest_report(
        backtest, performance, tmp_path, market_bars=sample_frame()
    )
    for path in (
        artifacts.json_path,
        artifacts.equity_csv_path,
        artifacts.orders_csv_path,
        artifacts.trades_csv_path,
        artifacts.positions_csv_path,
        artifacts.fees_csv_path,
        artifacts.html_path,
        artifacts.pdf_path,
        artifacts.svg_path,
    ):
        assert path.exists() and path.stat().st_size > 0
    assert "DyadTetrade" in artifacts.html_path.read_text(encoding="utf-8")
    svg = artifacts.svg_path.read_text(encoding="utf-8")
    assert "K-line / Trades" in svg
    assert "trade-marker" in svg
    assert "Net value / Benchmark" in svg
    assert "Drawdown" in svg
    assert artifacts.pdf_path.read_bytes().startswith(b"%PDF")


def test_report_trade_markers_match_the_displayed_symbol() -> None:
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        initial_cash=Decimal("100000"),
    )
    backtest = BacktestEngine().run(config, sample_frame())
    performance = analyze_performance(backtest.equity_frame())
    unrelated = Trade(
        trade_id="other-symbol-trade",
        order_id="other-symbol-order",
        symbol="000001.SZ",
        direction=Direction.BUY,
        quantity=100,
        price=Decimal("10"),
        timestamp=backtest.trades[0].timestamp,
    )
    svg = _equity_svg(
        performance.equity,
        sample_frame(),
        [*backtest.trades, unrelated],
        "600519.SH",
    )
    assert svg.count('class="trade-marker"') == len(backtest.trades)
