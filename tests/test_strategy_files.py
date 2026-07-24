from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.api.legacy_app import app
from dyadtetrade.api.routes import strategy_files as strategy_file_routes
from dyadtetrade.core.config import Settings, get_settings
from dyadtetrade.db.models import Base
from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.db.session import get_session
from dyadtetrade.modules.strategy.files import StrategyFileService
from dyadtetrade.modules.strategy.validation import smoke_backtest, validate_strategy_source

VALID_SOURCE = """from dyadtetrade.modules.strategy.base import Strategy, StrategyContext
from dyadtetrade.domain import Bar

class PreopenTestStrategy(Strategy):
    name = "preopen_test_strategy"
    version = "1.0.0"
    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        if context.get_position(bar.symbol) is None:
            context.buy(bar.symbol, 100)
"""


@pytest.fixture(autouse=True)
def isolated_strategy_market_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "market-data"
    duckdb_path = tmp_path / "research.duckdb"
    monkeypatch.setenv("DYADTETRADE_DATA_ROOT", str(data_root))
    monkeypatch.setenv("DYADTETRADE_DUCKDB_PATH", str(duckdb_path))
    get_settings.cache_clear()
    frame = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-05", periods=5, freq="B"),
            "symbol": ["600519.SH"] * 5,
            "open": [10.0, 10.1, 10.2, 10.3, 10.4],
            "high": [10.2, 10.3, 10.4, 10.5, 10.6],
            "low": [9.9, 10.0, 10.1, 10.2, 10.3],
            "close": [10.1, 10.2, 10.3, 10.4, 10.5],
            "volume": [1000] * 5,
            "amount": [10100, 10200, 10300, 10400, 10500],
            "period": ["1d"] * 5,
            "adjust": ["front"] * 5,
        }
    )
    ParquetStore(data_root).write(
        frame,
        symbol="600519.SH",
        period="1d",
        adjust="front",
        layer="bt_ready",
    )
    yield
    get_settings.cache_clear()


def test_strategy_ast_validation_and_path_security(tmp_path: Path) -> None:
    assert validate_strategy_source(VALID_SOURCE)["valid"] is True
    assert (
        validate_strategy_source("from xtquant import xttrader\n" + VALID_SOURCE)["valid"] is False
    )
    assert validate_strategy_source("def broken(:\n")["valid"] is False
    service = StrategyFileService(
        Settings(_env_file=None, user_strategies_path=tmp_path / "strategies")
    )
    with pytest.raises(ValueError, match="letters"):
        service.safe_path("../escape.py")
    with pytest.raises(ValueError, match="letters"):
        service.safe_path("bad.txt")
    path = tmp_path / "direct_strategy.py"
    path.write_text(VALID_SOURCE, encoding="utf-8")
    smoke = smoke_backtest(path, "600519.SH", {})
    assert smoke["passed"] is True


def test_strategy_file_validate_smoke_publish_and_subprocess_heartbeat(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        service = StrategyFileService(
            Settings(_env_file=None, user_strategies_path=tmp_path / "strategies")
        )
        async with sessions() as session:
            created = await service.create(session, "preopen_test_strategy", VALID_SOURCE)
            assert created["validation_status"] == "draft"
            validated = await service.validate(session, "preopen_test_strategy")
            assert validated["valid"] is True
            smoke = await service.smoke(
                session,
                "preopen_test_strategy",
                symbol="600519.SH",
                parameters={},
            )
            assert smoke["passed"] is True
            published = await service.publish(session, "preopen_test_strategy")
            assert published["published_version"] == 1
            try:
                await service.start(session, "preopen_test_strategy")
                await asyncio.sleep(1.3)
                running = await service.refresh_runtime(session, "preopen_test_strategy")
                assert running["runtime_status"] == "running"
                assert running["heartbeat_at"] is not None
            finally:
                stopped = await service.stop(session, "preopen_test_strategy")
                assert stopped["runtime_status"] == "stopped"
        await engine.dispose()

    asyncio.run(scenario())


def test_strategy_file_api_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        service = StrategyFileService(
            Settings(_env_file=None, user_strategies_path=tmp_path / "api-strategies")
        )
        monkeypatch.setattr(strategy_file_routes, "strategy_file_service", service)

        async def override_session():  # type: ignore[no-untyped-def]
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        try:
            with TestClient(app) as client:
                created = client.post(
                    "/api/v1/strategy-files",
                    json={"name": "api_preopen_strategy", "code": VALID_SOURCE},
                )
                assert created.status_code == 200
                assert (
                    client.get("/api/v1/strategy-files").json()[0]["name"] == "api_preopen_strategy"
                )
                assert client.get("/api/v1/strategy-files/api_preopen_strategy").status_code == 200
                assert client.post("/api/v1/strategy-files/api_preopen_strategy/validate").json()[
                    "valid"
                ]
                assert (
                    client.post(
                        "/api/v1/strategy-files/api_preopen_strategy/smoke-backtest",
                        json={"symbol": "600519.SH", "parameters": {}},
                    ).status_code
                    == 200
                )
                assert (
                    client.post("/api/v1/strategy-files/api_preopen_strategy/publish").status_code
                    == 200
                )
                assert (
                    client.post("/api/v1/strategy-files/api_preopen_strategy/start").status_code
                    == 200
                )
                assert (
                    client.post("/api/v1/strategy-files/api_preopen_strategy/stop").status_code
                    == 200
                )
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    asyncio.run(scenario())
