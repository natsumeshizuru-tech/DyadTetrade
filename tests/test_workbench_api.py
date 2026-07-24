from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dyadtetrade.api.legacy_app import app
from dyadtetrade.api.routes import paper_trading
from dyadtetrade.modules.paper_trading import PaperTradingService


def test_strategy_research_optimization_and_portfolio_apis() -> None:
    with TestClient(app) as client:
        strategies = client.get("/api/v1/strategies")
        assert strategies.status_code == 200
        assert {item["name"] for item in strategies.json()} >= {
            "buy_and_hold",
            "moving_average_cross",
            "equal_weight_rotation",
        }

        records = []
        for day in range(3):
            for index, symbol in enumerate(["A", "B", "C", "D", "E"]):
                date = f"2026-01-0{day + 1}"
                records.append(
                    {
                        "date": date,
                        "visible_at": date,
                        "symbol": symbol,
                        "factor": index,
                        "forward_return": index / 100,
                    }
                )
        factor = client.post(
            "/api/v1/research/factors/analyze",
            json={"records": records, "factor_columns": ["factor"], "groups": 5},
        )
        assert factor.status_code == 200
        assert factor.json()["summary"]["rank_ic_mean"] == pytest.approx(1.0)

        optimized = client.post(
            "/api/v1/optimizations/run",
            json={
                "parameter_space": {"fast": [2, 3, 4], "slow": [7, 8]},
                "target": {"fast": 3, "slow": 8},
                "method": "grid",
                "max_trials": 6,
            },
        )
        assert optimized.status_code == 200
        assert len(optimized.json()["trials"]) == 6

        allocated = client.post(
            "/api/v1/portfolios/allocate",
            json={
                "returns": {
                    "510300.SH": [0.01, -0.01, 0.02],
                    "510500.SH": [0.005, -0.002, 0.01],
                },
                "method": "equal",
            },
        )
        assert allocated.status_code == 200
        assert sum(allocated.json()["weights"].values()) <= 0.95


def test_paper_api_never_marks_order_as_live() -> None:
    paper_trading.paper_service = PaperTradingService()
    now = datetime.now(UTC).replace(microsecond=0)
    bar = {
        "symbol": "510300.SH",
        "timestamp": now.isoformat(),
        "open": 4.0,
        "high": 4.1,
        "low": 3.9,
        "close": 4.0,
        "volume": 100000,
    }
    with TestClient(app) as client:
        market = client.post("/api/v1/paper/market/bars", json=[bar])
        assert market.status_code == 200
        order = client.post(
            "/api/v1/paper/orders",
            json={
                "symbol": "510300.SH",
                "direction": "buy",
                "quantity": 100,
                "strategy_id": "api-test",
                "idempotency_key": "api-paper-order",
            },
        )
        assert order.status_code == 201
        assert order.json()["live_order"] is False
        next_bar = dict(bar)
        next_bar["timestamp"] = (now + timedelta(minutes=1)).isoformat()
        filled = client.post("/api/v1/paper/market/bars", json=[next_bar])
        assert filled.status_code == 200
        assert len(filled.json()["trades"]) == 1
        assert filled.json()["mode"] == "paper"
