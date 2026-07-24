from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from dyadtetrade.domain import Bar, Direction, Interval, OrderRequest, OrderStatus
from dyadtetrade.integrations.xttrader_gateway import (
    LiveTradingDisabledError,
    XtTraderGatewaySkeleton,
)
from dyadtetrade.modules.optimization import OptimizationEngine
from dyadtetrade.modules.paper_trading import PaperTradingService
from dyadtetrade.modules.portfolio import (
    PortfolioConstraints,
    allocate,
    create_rebalance_plan,
    generate_order_intents,
)
from dyadtetrade.modules.risk import RiskEngine, RiskLimits


def paper_bar(timestamp: datetime, price: str = "10", volume: int = 10000) -> Bar:
    value = Decimal(price)
    return Bar(
        "510300.SH",
        timestamp,
        Interval.MINUTE_1,
        value,
        value,
        value,
        value,
        volume,
    )


def test_optimization_reports_neighborhood_and_out_of_sample() -> None:
    def objective(parameters: dict[str, float | int], segment: str) -> float:
        fast = float(parameters["fast"])
        slow = float(parameters["slow"])
        base = 1.0 - abs(fast - 3) * 0.1 - abs(slow - 8) * 0.05
        return base if segment == "in_sample" else base - abs(fast + slow - 11) * 0.02

    result = OptimizationEngine().run(
        {"fast": [2, 3, 4], "slow": [7, 8, 9]},
        objective,
        method="grid",
        max_trials=9,
    )
    assert len(result.trials) == 9
    assert result.best.out_of_sample_score <= result.best.in_sample_score
    assert np.isfinite(result.neighborhood_stability)
    assert result.warnings
    optuna_result = OptimizationEngine().run(
        {"fast": [2, 3, 4], "slow": [7, 8, 9]},
        objective,
        method="optuna",
        max_trials=3,
        random_seed=7,
    )
    assert len(optuna_result.trials) == 3


def test_portfolio_allocations_and_order_intents() -> None:
    returns = pd.DataFrame(
        {
            "510300.SH": [0.01, -0.005, 0.008, 0.004],
            "510500.SH": [0.005, -0.002, 0.004, 0.003],
            "159915.SZ": [0.02, -0.01, 0.015, -0.003],
        }
    )
    weights = allocate(returns, method="inverse_volatility")
    assert 0 < weights.sum() <= 0.95
    assert (weights >= 0).all()
    intents = generate_order_intents(
        weights,
        pd.Series(0.0, index=weights.index),
        pd.Series({"510300.SH": 4.0, "510500.SH": 6.0, "159915.SZ": 2.0}),
        total_assets=1_000_000,
    )
    assert intents
    assert all(intent.quantity % 100 == 0 for intent in intents)


def test_portfolio_industry_turnover_risk_budget_and_rebalance_plan() -> None:
    returns = pd.DataFrame(
        {
            "A": [0.01, -0.01, 0.02, -0.005, 0.01],
            "B": [0.005, -0.004, 0.006, -0.003, 0.004],
            "C": [0.02, -0.015, 0.01, -0.01, 0.02],
            "D": [0.003, -0.002, 0.004, -0.001, 0.002],
        }
    )
    constraints = PortfolioConstraints(
        max_single_weight=0.4,
        max_total_weight=0.8,
        cash_buffer=0.2,
        turnover_limit=0.5,
        industry_limits={"tech": 0.4, "finance": 0.4},
        risk_budgets={"A": 1, "B": 2, "C": 1, "D": 2},
    )
    industries = pd.Series({"A": "tech", "B": "tech", "C": "finance", "D": "finance"})
    weights = allocate(
        returns,
        method="risk_parity",
        constraints=constraints,
        current_weights=pd.Series({"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2}),
        industries=industries,
    )
    assert weights.sum() == pytest.approx(0.8)
    assert weights[["A", "B"]].sum() <= 0.4 + 1e-6
    assert weights[["C", "D"]].sum() <= 0.4 + 1e-6
    plan = create_rebalance_plan(
        weights,
        pd.Series(0.0, index=weights.index),
        pd.Series(10.0, index=weights.index),
        total_assets=100_000,
        effective_at=datetime(2026, 1, 5, tzinfo=UTC),
        industries=industries,
        constraints=constraints,
    )
    assert plan.sent_to_broker is False
    assert plan.expected_turnover <= constraints.turnover_limit + 1e-9
    assert plan.cash_buffer == pytest.approx(0.2)


def test_paper_trading_oms_risk_and_t1_without_live_gateway() -> None:
    risk = RiskEngine(
        RiskLimits(max_quote_age=timedelta(minutes=5), max_order_value=Decimal("100000"))
    )
    paper = PaperTradingService(initial_cash=Decimal("100000"), risk_engine=risk)
    day1 = datetime(2026, 1, 5, 9, 31, tzinfo=UTC)
    paper.on_bars(day1, [paper_bar(day1)])
    request = OrderRequest(
        "510300.SH",
        Direction.BUY,
        1000,
        strategy_id="ma",
        account_id="paper",
        idempotency_key="paper-1",
    )
    order = paper.submit(request)
    assert order.status is OrderStatus.SUBMITTED
    assert paper.submit(request) is order
    day1_next = datetime(2026, 1, 5, 9, 32, tzinfo=UTC)
    paper.on_bars(day1_next, [paper_bar(day1_next, "10.1")])
    assert paper.broker.account.positions["510300.SH"].available_quantity == 0
    day2 = datetime(2026, 1, 6, 9, 31, tzinfo=UTC)
    paper.on_bars(day2, [paper_bar(day2, "10.2")])
    assert paper.broker.account.positions["510300.SH"].available_quantity == 1000
    snapshot = paper.snapshot()
    assert snapshot["mode"] == "paper"
    assert len(snapshot["trades"]) == 1
    assert paper.last_reconciled_at == day2


def test_stale_market_and_live_gateway_fail_closed() -> None:
    paper = PaperTradingService()
    old = datetime(2026, 1, 5, 9, 31, tzinfo=UTC)
    paper.on_bars(old, [paper_bar(old)])
    paper.broker.set_time(old + timedelta(minutes=1))
    rejected = paper.submit(OrderRequest("510300.SH", Direction.BUY, 100))
    assert rejected.status is OrderStatus.RISK_REJECTED
    gateway = XtTraderGatewaySkeleton(environment="prod", connected=True)
    assert gateway.connection_status()["live_order_capability"] is False
    with pytest.raises(LiveTradingDisabledError):
        gateway.submit_order(OrderRequest("510300.SH", Direction.BUY, 100))


def test_risk_weight_limit_board_and_intraday_health_controls() -> None:
    now = datetime(2026, 1, 5, 9, 31, tzinfo=UTC)
    limited = Bar(
        "510300.SH",
        now,
        Interval.MINUTE_1,
        Decimal("10"),
        Decimal("10"),
        Decimal("10"),
        Decimal("10"),
        10000,
        limit_up=Decimal("10"),
    )
    paper = PaperTradingService(initial_cash=Decimal("100000"))
    paper.on_bars(now, [limited])
    rejected_limit = paper.submit(OrderRequest("510300.SH", Direction.BUY, 100))
    assert rejected_limit.reject_reason and "LIMIT_UP" in rejected_limit.reject_reason

    normal = paper_bar(now + timedelta(minutes=1))
    paper.on_bars(normal.timestamp, [normal])
    rejected_weight = paper.submit(OrderRequest("510300.SH", Direction.BUY, 3000))
    assert rejected_weight.reject_reason and "MAX_SYMBOL_WEIGHT" in rejected_weight.reject_reason

    paper.heartbeat_strategy("stale-strategy", now)
    findings = paper.risk.monitor_intraday(
        now=now + timedelta(minutes=3),
        account=paper.broker.account,
        last_market_at=now,
        gateway_connected=False,
        strategy_heartbeats=paper.strategy_heartbeats,
        active_orders=paper.oms.active_orders(),
        previous_total_assets=Decimal("200000"),
    )
    codes = {item.code for item in findings}
    assert {"MARKET_DATA_INTERRUPTED", "GATEWAY_DISCONNECTED"}.issubset(codes)
    assert "STRATEGY_HEARTBEAT_LOST" in codes
    assert "ASSET_CHANGE_ANOMALY" in codes
    assert paper.risk.switches.global_stop is True
    assert paper.risk.switches.cancel_only is True
