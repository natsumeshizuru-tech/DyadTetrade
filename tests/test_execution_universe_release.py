from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.core.config import ExecutionMode, Settings
from dyadtetrade.db.models import (
    ExecutionUniverseMemberRecord,
    ExecutionUniverseReleaseRecord,
    Instrument,
)
from dyadtetrade.db.session import Base
from dyadtetrade.modules.execution.universe import (
    ExecutionUniverseFilters,
    ExecutionUniverseService,
    StrategyExecutionReleaseService,
    _instrument_rejection,
    _universe_hash,
)


def test_execution_universe_distinguishes_future_expiry_from_delisting() -> None:
    instrument = Instrument(
        symbol="600519.SH",
        exchange="SH",
        asset_type="stock",
        name="贵州茅台",
        list_date="20010827",
        delist_date="99999999",
        lot_size=100,
        price_tick=Decimal("0.01"),
        is_active=True,
    )
    filters = ExecutionUniverseFilters()
    assert (
        _instrument_rejection(
            instrument,
            latest_date="20260713",
            required_data_date="20260713",
            listed_cutoff="20260623",
            filters=filters,
        )
        is None
    )
    instrument.delist_date = "20260712"
    assert (
        _instrument_rejection(
            instrument,
            latest_date="20260713",
            required_data_date="20260713",
            listed_cutoff="20260623",
            filters=filters,
        )
        == "delisting"
    )


@pytest.mark.asyncio
async def test_execution_universe_review_activation_integrity_and_strategy_release(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'execution-universe.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:10]
    release_id = f"universe-test-{suffix}"
    symbols = [f"91{suffix[:4]}.SH", f"92{suffix[:4]}.SZ"]
    now = datetime.now(UTC)
    trade_date = now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    filters: dict[str, object] = {"test": True}
    snapshot_hash = _universe_hash(
        symbols,
        trade_date=trade_date,
        required_data_date=trade_date,
        filters=filters,
    )
    strategy_release_id = ""
    async with sessions() as session:
        session.add(
            ExecutionUniverseReleaseRecord(
                release_id=release_id,
                trade_date=trade_date,
                execution_mode="qmt_sim",
                scope="test",
                status="draft",
                symbol_count=len(symbols),
                snapshot_hash=snapshot_hash,
                required_data_date=trade_date,
                filters_json=json.dumps(filters),
                rejection_counts_json="{}",
            )
        )
        session.add_all(
            [
                ExecutionUniverseMemberRecord(
                    release_id=release_id,
                    symbol=symbol,
                    exchange=symbol.split(".")[-1],
                    asset_type="stock",
                    data_latest_date=trade_date,
                    quality_status="passed",
                )
                for symbol in symbols
            ]
        )
        await session.commit()
        reviewed = await ExecutionUniverseService.review(session, release_id, reviewer="test")
        assert reviewed["status"] == "reviewed"
        with pytest.raises(PermissionError, match="confirmation"):
            await ExecutionUniverseService.activate(
                session,
                release_id,
                approver="test",
                confirmation="wrong",
            )
        active = await ExecutionUniverseService.activate(
            session,
            release_id,
            approver="test",
            confirmation="I_CONFIRM_QMT_SIM_UNIVERSE",
        )
        assert active["status"] == "active"
        loaded, snapshot = await ExecutionUniverseService.load_active_symbols(
            session,
            release_id,
            execution_mode=ExecutionMode.QMT_SIM,
        )
        assert loaded == frozenset(symbols)
        assert snapshot["snapshot_hash"] == snapshot_hash

        strategy = await StrategyExecutionReleaseService.create(
            session,
            execution_mode=ExecutionMode.QMT_SIM,
            strategy_id=f"strategy-{suffix}",
            strategy_name="buy_and_hold",
            strategy_version="1.0.0",
            universe_release_id=release_id,
            parameters={"symbol": symbols[0]},
            max_order_notional=Decimal("1000"),
            max_daily_notional=Decimal("2000"),
        )
        strategy_release_id = str(strategy["release_id"])
        activated_strategy = await StrategyExecutionReleaseService.activate(
            session,
            strategy_release_id,
            approver="test",
            confirmation="I_CONFIRM_AUTOMATED_QMT_SIM_STRATEGY",
        )
        assert activated_strategy["status"] == "active"
        revoked_strategy = await StrategyExecutionReleaseService.revoke(
            session, strategy_release_id, actor="test"
        )
        assert revoked_strategy["status"] == "revoked"
    assert loaded == frozenset(symbols)
    await engine.dispose()


def test_dynamic_full_market_live_configuration_is_daily_and_fail_closed(
    tmp_path: Path,
) -> None:
    base = {
        "execution_mode": "qmt_live",
        "enable_live_trading": True,
        "enable_qmt_live_trading": True,
        "qmt_account_id": "live-account",
        "qmt_live_account_whitelist": ["live-account"],
        "qmt_userdata_path": tmp_path,
        "qmt_live_confirmation": "I_CONFIRM_QMT_LIVE",
        "qmt_live_release_date": date.today().isoformat(),
        "enable_dynamic_execution_universe": True,
        "execution_universe_release_id": "universe-release",
        "enable_full_market_live_trading": True,
        "full_market_live_confirmation": "I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE",
        "full_market_live_release_date": date.today().isoformat(),
    }
    Settings(_env_file=None, **base).validate_execution_safety()
    with pytest.raises(RuntimeError, match="FULL_MARKET_LIVE_CONFIRMATION"):
        Settings(
            _env_file=None,
            **{**base, "full_market_live_confirmation": ""},
        ).validate_execution_safety()


def test_automated_live_execution_requires_an_independent_daily_confirmation(
    tmp_path: Path,
) -> None:
    base = {
        "execution_mode": "qmt_live",
        "enable_live_trading": True,
        "enable_qmt_live_trading": True,
        "qmt_account_id": "live-account",
        "qmt_live_account_whitelist": ["live-account"],
        "qmt_userdata_path": tmp_path,
        "qmt_live_confirmation": "I_CONFIRM_QMT_LIVE",
        "qmt_live_release_date": date.today().isoformat(),
        "allowed_symbols": ["600519.SH"],
        "enable_automated_strategy_execution": True,
        "automated_live_confirmation": "I_CONFIRM_AUTOMATED_LIVE_EXECUTION",
        "automated_live_release_date": date.today().isoformat(),
    }
    Settings(_env_file=None, **base).validate_execution_safety()
    with pytest.raises(RuntimeError, match="AUTOMATED_LIVE_CONFIRMATION"):
        Settings(
            _env_file=None,
            **{**base, "automated_live_confirmation": ""},
        ).validate_execution_safety()
    with pytest.raises(RuntimeError, match="AUTOMATED_LIVE_RELEASE_DATE"):
        Settings(
            _env_file=None,
            **{**base, "automated_live_release_date": "2020-01-01"},
        ).validate_execution_safety()
