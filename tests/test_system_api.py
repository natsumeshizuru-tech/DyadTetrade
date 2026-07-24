import pytest
from fastapi.testclient import TestClient

from dyadtetrade.api.main import app
from dyadtetrade.api.routes import system as system_routes
from dyadtetrade.integrations.xtquant import XtQuantStatus


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "test-request"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"] == "test-request"


def test_module_registry_contains_all_capabilities() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/modules")
    assert response.status_code == 200
    keys = {item["key"] for item in response.json()}
    assert {"realtime", "paper", "broker", "oms", "execution", "risk", "monitoring"} <= keys
    assert keys.isdisjoint({"research", "backtest", "optimization", "market_data"})


def test_xtquant_unavailable_is_reported_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        system_routes,
        "get_xtquant_status",
        lambda: XtQuantStatus(
            installed=False,
            path=None,
            connected=False,
            sector_count=0,
            live_trading_enabled=False,
            error="XtQuant unavailable",
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/integrations/xtquant")
    assert response.status_code == 200
    assert response.json() == {
        "installed": False,
        "path": None,
        "connected": False,
        "sector_count": 0,
        "live_trading_enabled": False,
        "error": "XtQuant unavailable",
    }


def test_readiness_and_metrics_keep_live_trading_disabled() -> None:
    with TestClient(app) as client:
        ready = client.get("/api/v1/ready")
        metrics = client.get("/api/v1/metrics")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["live_trading_enabled"] is False
    assert metrics.status_code == 200
    assert "dyadtetrade_http_requests_total" in metrics.text


def test_monitor_websocket_streams_read_only_status() -> None:
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws/monitor") as websocket:
        message = websocket.receive_json()
    assert message["type"] == "monitor"
    assert message["live_trading_enabled"] is False
    assert "paper_account" in message
    assert "paper_risk" in message


def test_default_product_api_excludes_research_and_historical_data_management() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    paths = set(schema["paths"])
    forbidden_prefixes = (
        "/api/v1/backtests",
        "/api/v1/research",
        "/api/v1/optimizations",
        "/api/v1/portfolios",
        "/api/v1/strategy-files",
        "/api/v1/market/history",
        "/api/v1/universe/catalog",
    )
    assert not any(path.startswith(forbidden_prefixes) for path in paths)
    assert "/api/v1/execution/status" in paths
    assert "/api/v1/monitoring/strategies" in paths
    assert "/api/v1/realtime/status" in paths
    assert "/api/v1/paper/snapshot" in paths
