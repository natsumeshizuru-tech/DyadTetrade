from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from dyadtetrade.api.public_app import app
from dyadtetrade.api.routes import paper_trading
from dyadtetrade.domain import Bar, Interval
from dyadtetrade.modules.paper_trading import PaperStrategyRunner, PaperTradingService


def bar(at: datetime, price: str = "10") -> Bar:
    value = Decimal(price)
    return Bar(
        "510300.SH",
        at,
        Interval.MINUTE_1,
        value,
        value,
        value,
        value,
        100_000,
    )


def test_same_strategy_sdk_runs_through_paper_risk_and_oms() -> None:
    service = PaperTradingService(initial_cash=Decimal("100000"))
    runner = PaperStrategyRunner(
        service,
        "buy_and_hold",
        "paper-buy-hold",
        {"symbol": "510300.SH", "quantity": 100},
    )
    runner.start()
    first = datetime(2026, 1, 5, 9, 31, tzinfo=UTC)
    runner.on_bars(first, [bar(first)])
    assert len(service.oms.orders) == 1
    assert len(service.oms.trades) == 0
    runner.on_bars(first + timedelta(minutes=1), [bar(first + timedelta(minutes=1), "10.1")])
    assert len(service.oms.trades) == 1
    assert runner.status()["mode"] == "paper"
    assert runner.status()["live_order_capability"] is False
    state = runner.snapshot()
    restored = PaperStrategyRunner(
        service,
        "buy_and_hold",
        "paper-buy-hold",
        {"symbol": "510300.SH", "quantity": 100},
    )
    restored.restore(state)
    assert restored.snapshot().payload["submitted"] is True


def test_paper_strategy_api_start_status_stop_never_exposes_live_send() -> None:
    paper_trading.paper_service = PaperTradingService(initial_cash=Decimal("100000"))
    paper_trading.paper_runners.clear()
    with TestClient(app) as client:
        started = client.post(
            "/api/v1/paper/strategies/start",
            json={
                "strategy_name": "buy_and_hold",
                "strategy_id": "api-paper-strategy",
                "parameters": {"symbol": "510300.SH", "quantity": 100},
            },
        )
        assert started.status_code == 201
        assert started.json()["running"] is True
        assert started.json()["live_order_capability"] is False
        statuses = client.get("/api/v1/paper/strategies")
        assert statuses.status_code == 200
        assert statuses.json()[0]["mode"] == "paper"
        stopped = client.post("/api/v1/paper/strategies/api-paper-strategy/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False
