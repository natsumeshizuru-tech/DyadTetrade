from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.db.models import StrategyRunRecord
from dyadtetrade.db.session import Base
from dyadtetrade.domain import Bar, Direction, Interval, OrderRequest
from dyadtetrade.modules.paper_trading import PaperStrategyRunner, PaperTradingService
from dyadtetrade.modules.paper_trading.repository import (
    recover_paper_runners,
    recover_paper_state,
    save_paper_runners,
    save_paper_state,
)


def paper_bar(timestamp: datetime, price: str = "10", volume: int = 10_000) -> Bar:
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


@pytest.mark.asyncio
async def test_paper_account_orders_trades_and_positions_recover(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'paper.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PaperTradingService(initial_cash=Decimal("100000"))
    start = datetime(2026, 1, 5, 9, 31, tzinfo=UTC)
    service.on_bars(start, [paper_bar(start)])
    service.submit(
        OrderRequest(
            "510300.SH",
            Direction.BUY,
            100,
            strategy_id="recovery-test",
            account_id="paper",
            idempotency_key="recovery-order",
        )
    )
    service.on_bars(start + timedelta(minutes=1), [paper_bar(start + timedelta(minutes=1))])
    async with factory() as session:
        await save_paper_state(session, service)
        recovered = PaperTradingService(initial_cash=Decimal("1"))
        assert await recover_paper_state(session, recovered)
        assert recovered.broker.account.total_assets == service.broker.account.total_assets
        assert recovered.broker.account.positions["510300.SH"].quantity == 100
        assert len(recovered.oms.orders) == 1
        assert len(recovered.oms.trades) == 1
        duplicate = recovered.submit(
            OrderRequest(
                "510300.SH",
                Direction.BUY,
                100,
                strategy_id="recovery-test",
                account_id="paper",
                idempotency_key="recovery-order",
            )
        )
        assert duplicate.order_id in recovered.oms.orders
    await engine.dispose()


@pytest.mark.asyncio
async def test_running_paper_strategy_state_recovers_after_restart(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'strategy.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PaperTradingService(initial_cash=Decimal("100000"))
    runner = PaperStrategyRunner(
        service,
        "buy_and_hold",
        "persistent-paper-strategy",
        {"symbol": "510300.SH", "quantity": 100},
    )
    runner.start()
    start = datetime(2026, 1, 5, 9, 31, tzinfo=UTC)
    runner.on_bars(start, [paper_bar(start)])
    async with factory() as session:
        await save_paper_state(session, service)
        await save_paper_runners(session, {runner.strategy.strategy_id: runner})
        restarted_service = PaperTradingService(initial_cash=Decimal("1"))
        assert await recover_paper_state(session, restarted_service)
        recovered = await recover_paper_runners(session, restarted_service)
    recovered_runner = recovered["persistent-paper-strategy"]
    assert recovered_runner.running is True
    assert recovered_runner.snapshot().payload["submitted"] is True
    assert recovered_runner.status()["live_order_capability"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_tick_strategy_state_migrates_from_v1_to_bounded_v11(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tick-strategy.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PaperTradingService()
    runner = PaperStrategyRunner(
        service,
        "tick_momentum_small_cap",
        "persistent-tick-strategy",
        {
            "symbols": ["600001.SH", "000001.SZ"],
            "float_shares": {"600001.SH": 100.0, "000001.SZ": 200.0},
            "small_cap_n": 2,
            "top_n": 1,
        },
    )
    runner.start()
    async with factory() as session:
        await save_paper_runners(session, {runner.strategy.strategy_id: runner})
        row = await session.scalar(
            select(StrategyRunRecord).where(
                StrategyRunRecord.strategy_instance_id == "persistent-tick-strategy"
            )
        )
        assert row is not None
        row.strategy_version = "1.0.0"
        await session.commit()
        recovered = await recover_paper_runners(session, PaperTradingService())
        await save_paper_runners(session, recovered)
        await session.refresh(row)
    assert recovered["persistent-tick-strategy"].strategy.version == "1.2.0"
    assert recovered["persistent-tick-strategy"].parameters["rebalance_every_ticks"] == 3000
    assert row.strategy_version == "1.2.0"
    await engine.dispose()


@pytest.mark.asyncio
async def test_tick_strategy_recovery_keeps_only_latest_singleton(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tick-singleton.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PaperTradingService()
    parameters = {
        "symbols": ["600001.SH", "000001.SZ"],
        "float_shares": {"600001.SH": 100.0, "000001.SZ": 200.0},
        "small_cap_n": 2,
        "top_n": 1,
    }
    older = PaperStrategyRunner(
        service, "tick_momentum_small_cap", "tick-singleton-older", parameters
    )
    latest = PaperStrategyRunner(
        service, "tick_momentum_small_cap", "tick-singleton-latest", parameters
    )
    older.start()
    latest.start()
    async with factory() as session:
        await save_paper_runners(
            session,
            {older.strategy.strategy_id: older, latest.strategy.strategy_id: latest},
        )
        older_row = await session.scalar(
            select(StrategyRunRecord).where(
                StrategyRunRecord.strategy_instance_id == "tick-singleton-older"
            )
        )
        latest_row = await session.scalar(
            select(StrategyRunRecord).where(
                StrategyRunRecord.strategy_instance_id == "tick-singleton-latest"
            )
        )
        assert older_row is not None and latest_row is not None
        older_row.updated_at = datetime(2026, 1, 1)
        latest_row.updated_at = datetime(2026, 1, 2)
        await session.commit()
        recovered = await recover_paper_runners(session, PaperTradingService())
        await session.refresh(older_row)
    assert list(recovered) == ["tick-singleton-latest"]
    assert older_row.status == "stopped"
    await engine.dispose()
