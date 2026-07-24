from fastapi.testclient import TestClient

from dyadtetrade.api.legacy_app import app
from dyadtetrade.api.routes.market_data import get_gateway
from dyadtetrade.integrations.xtquant import XtQuantGateway
from tests.test_market_data import FakeXtData


def test_market_universe_without_persistence() -> None:
    app.dependency_overrides[get_gateway] = lambda: XtQuantGateway(FakeXtData())
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/market/universe?persist=false")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "count": 3,
        "symbols": ["000001.SZ", "430047.BJ", "600519.SH"],
    }
