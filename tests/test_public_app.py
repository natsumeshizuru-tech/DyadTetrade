from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dyadtetrade.api.public_app import app
from dyadtetrade.core.config import ExecutionMode, Settings


def test_public_app_exposes_trading_routes_without_research_routes() -> None:
    with TestClient(app) as client:
        health = client.get("/api/v1/system/health")
        assert health.status_code == 200
        assert health.json()["edition"] == "public-trading"
        assert health.json()["live_trading_enabled"] is False
        assert health.json()["real_orders_sent"] == 0
        assert client.get("/api/v1/research/factors/definitions").status_code == 404
        assert client.get("/api/v1/backtests/tasks").status_code == 404
        assert client.get("/api/v1/optimizations/tasks").status_code == 404


def test_public_execution_policy_rejects_qmt_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    from dyadtetrade.api import public_app

    monkeypatch.setattr(
        public_app,
        "get_settings",
        lambda: Settings(execution_mode=ExecutionMode.QMT_SIM, enable_qmt_sim_trading=True),
    )
    with pytest.raises(RuntimeError, match="public app supports only"):
        public_app._assert_public_execution_policy()
