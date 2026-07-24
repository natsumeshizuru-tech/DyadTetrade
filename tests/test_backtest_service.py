from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.db.models import (
    BacktestDailyEquityRecord,
    BacktestOrderRecord,
    BacktestTradeRecord,
    DataFile,
)
from dyadtetrade.db.session import Base
from dyadtetrade.modules.backtest.repository import result_detail
from dyadtetrade.modules.backtest.schemas import BacktestConfig
from dyadtetrade.modules.backtest.service import BacktestService
from tests.test_backtest_engine import sample_frame


@pytest.mark.asyncio
async def test_backtest_task_persistence_idempotency_and_reports(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'backtest.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = BacktestService(report_root=tmp_path / "reports")
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
        initial_cash=Decimal("100000"),
    )
    async with factory() as session:
        task, created = await service.create_task(session, config)
        duplicate, duplicate_created = await service.create_task(session, config)
        assert created and not duplicate_created and task.id == duplicate.id
        session.add(
            DataFile(
                symbol="600519.SH",
                period="1d",
                adjust="front",
                layer="bt_ready",
                file_path="fixture.parquet",
                checksum="new-data-version",
            )
        )
        await session.commit()
        refreshed_task, refreshed_created = await service.create_task(session, config)
        assert refreshed_created and refreshed_task.id != task.id
        completed = await service.run_task(session, task.id, frame=sample_frame())
        assert completed.status == "succeeded"
        assert completed.progress == 100
        assert completed.result_id is not None
        detail = await result_detail(session, completed.result_id)
        assert detail is not None
        assert len(detail["orders"]) == 1
        assert len(detail["trades"]) == 1
        assert len(detail["equity"]) == 4
        assert isinstance(detail["orders"][0]["average_fill_price"], str)
        assert isinstance(detail["trades"][0]["price"], str)
        assert isinstance(detail["equity"][0]["cash"], str)
        for model in (BacktestOrderRecord, BacktestTradeRecord, BacktestDailyEquityRecord):
            assert await session.scalar(select(func.count()).select_from(model))
        reports = detail["reports"]
        assert isinstance(reports, dict)
        assert Path(str(reports["html"])).exists()
    await engine.dispose()


@pytest.mark.asyncio
async def test_pending_backtest_can_be_cancelled(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config = BacktestConfig(
        strategy_name="buy_and_hold",
        strategy_parameters={"symbol": "600519.SH", "quantity": 100},
        symbols=["600519.SH"],
    )
    async with factory() as session:
        task, _ = await BacktestService().create_task(session, config)
        cancelled = await BacktestService().cancel_task(session, task.id)
        assert cancelled.status == "cancelled"
        assert cancelled.cancel_requested
    await engine.dispose()
