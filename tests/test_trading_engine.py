from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from dyadtetrade.core.config import ExecutionMode, Settings
from dyadtetrade.db.models import Base, TradingRiskStateRecord
from dyadtetrade.domain import RiskOperatingState
from dyadtetrade.modules.risk import TradingSwitches
from dyadtetrade.trading import engine as trading_engine
from dyadtetrade.trading.engine import TradingEngineService


class FakeRuntime:
    def __init__(
        self,
        consistency: dict[str, object],
        *,
        start_error: bool = False,
        reconcile_error: bool = False,
    ) -> None:
        self.gateway = SimpleNamespace(
            connected=True,
            subscribed=True,
            account_healthy=True,
            recovery_required=False,
            positions={},
            safety=SimpleNamespace(switches=TradingSwitches()),
        )
        self.last_reconciliation = consistency
        self.last_error: str | None = None
        self.started = False
        self.stopped = False
        self.reconcile_calls = 0
        self.start_error = start_error
        self.reconcile_error = reconcile_error

    async def start(self) -> None:
        self.started = True
        if self.start_error:
            raise RuntimeError("simulated runtime startup failure")

    async def stop(self) -> None:
        self.stopped = True

    async def reconcile(self) -> dict[str, object]:
        self.reconcile_calls += 1
        if self.reconcile_error:
            raise RuntimeError("simulated runtime reconciliation failure")
        if self.last_reconciliation.get("consistent") is True:
            self.gateway.recovery_required = False
        return {"ledger_consistency": self.last_reconciliation}

    def snapshot(self) -> dict[str, object]:
        return {
            "execution_mode": "qmt_sim",
            "connected": True,
            "subscribed": True,
            "account_healthy": True,
            "reconciliation": self.last_reconciliation,
            "last_error": self.last_error,
        }


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        execution_mode=ExecutionMode.QMT_SIM,
        enable_qmt_sim_trading=True,
        enable_live_trading=False,
        qmt_account_id="SIMULATION-ACCOUNT",
        qmt_userdata_path=tmp_path,
        allowed_symbols=["510300.SH"],
    )


@pytest.mark.asyncio
async def test_service_rejects_client_id_that_qmt_order_remark_would_truncate(
    tmp_path: Path,
) -> None:
    service = TradingEngineService(settings(tmp_path))

    with pytest.raises(ValueError, match="1 to 48 characters"):
        await service.submit_order(
            {
                "symbol": "510300.SH",
                "direction": "buy",
                "quantity": 100,
                "limit_price": "4.000",
                "strategy_id": "manual",
                "client_order_id": "x" * 49,
                "idempotency_key": "valid-key",
                "confirm_simulated_order": True,
            }
        )


async def build_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consistency: dict[str, object],
    *,
    start_error: bool = False,
    reconcile_error: bool = False,
) -> tuple[TradingEngineService, FakeRuntime, async_sessionmaker[Any], AsyncEngine]:
    database = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(database, expire_on_commit=False)

    async def initialized() -> None:
        return None

    monkeypatch.setattr(trading_engine, "SessionFactory", sessions)
    monkeypatch.setattr(trading_engine, "initialize_database", initialized)
    service = TradingEngineService(settings(tmp_path))
    runtime = FakeRuntime(
        consistency,
        start_error=start_error,
        reconcile_error=reconcile_error,
    )
    service.runtime = cast(Any, runtime)
    return service, runtime, sessions, database


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "switch_name"),
    [
        (RiskOperatingState.HALTED, "global_stop"),
        (RiskOperatingState.CANCEL_ONLY, "cancel_only"),
        (RiskOperatingState.REDUCE_ONLY, "no_new_positions"),
    ],
)
async def test_restricted_risk_state_survives_restart_and_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: RiskOperatingState,
    switch_name: str,
) -> None:
    service, runtime, sessions, database = await build_service(
        tmp_path,
        monkeypatch,
        {
            "consistent": True,
            "unmatched_local_active_count": 0,
            "unmatched_broker_active_count": 0,
        },
    )
    async with sessions() as session:
        session.add(
            TradingRiskStateRecord(
                account_key=service._account_key,
                execution_mode="qmt_sim",
                state=state.value,
                reason="operator restriction",
                version=7,
                changed_by="operator",
                changed_at=datetime.now(UTC).replace(tzinfo=None),
                reconciled_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()

    await service.start()
    assert service.risk.state is state
    assert getattr(runtime.gateway.safety.switches, switch_name) is True

    await service.reconcile()
    assert service.risk.state is state
    await service.stop()
    assert service.risk.state is state

    async with sessions() as session:
        persisted = await session.scalar(select(TradingRiskStateRecord))
        assert persisted is not None and persisted.state == state.value
    await database.dispose()


@pytest.mark.asyncio
async def test_reconciliation_mismatch_fails_closed_until_fresh_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mismatch = {
        "consistent": False,
        "unmatched_local_active_count": 1,
        "unmatched_broker_active_count": 0,
    }
    service, runtime, _, database = await build_service(tmp_path, monkeypatch, mismatch)

    await service.start()
    assert service.risk.state is RiskOperatingState.HALTED
    assert runtime.gateway.safety.switches.global_stop is True
    assert "reconciliation mismatch" in service.risk.reason

    runtime.last_reconciliation = {
        "consistent": True,
        "unmatched_local_active_count": 0,
        "unmatched_broker_active_count": 0,
    }
    await service.transition_risk(
        RiskOperatingState.RECOVERY,
        "operator starts fresh reconciliation",
        "operator",
    )
    await service.reconcile()
    assert service.risk.state is RiskOperatingState.NORMAL
    assert runtime.gateway.safety.switches.global_stop is False

    await service.stop()
    await database.dispose()


@pytest.mark.asyncio
async def test_startup_failure_restores_persisted_halted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, runtime, sessions, database = await build_service(
        tmp_path,
        monkeypatch,
        {"consistent": True},
        start_error=True,
    )
    async with sessions() as session:
        session.add(
            TradingRiskStateRecord(
                account_key=service._account_key,
                execution_mode="qmt_sim",
                state=RiskOperatingState.HALTED.value,
                reason="operator restriction",
                version=3,
                changed_by="operator",
                changed_at=datetime.now(UTC).replace(tzinfo=None),
                reconciled_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()

    with pytest.raises(RuntimeError, match="startup failure"):
        await service.start()

    assert service.risk.state is RiskOperatingState.HALTED
    assert runtime.gateway.safety.switches.global_stop is True
    assert runtime.stopped is True
    async with sessions() as session:
        persisted = await session.scalar(select(TradingRiskStateRecord))
        assert persisted is not None and persisted.state == RiskOperatingState.HALTED.value
    await database.dispose()


@pytest.mark.asyncio
async def test_manual_reconciliation_failure_restores_halted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, runtime, sessions, database = await build_service(
        tmp_path,
        monkeypatch,
        {"consistent": True},
    )
    await service.start()
    await service.transition_risk(
        RiskOperatingState.HALTED,
        "operator restriction",
        "operator",
    )
    runtime.reconcile_error = True

    with pytest.raises(RuntimeError, match="reconciliation failure"):
        await service.reconcile()

    assert service.risk.state is RiskOperatingState.HALTED
    assert runtime.gateway.safety.switches.global_stop is True
    async with sessions() as session:
        persisted = await session.scalar(select(TradingRiskStateRecord))
        assert persisted is not None and persisted.state == RiskOperatingState.HALTED.value

    await service.stop()
    await database.dispose()


@pytest.mark.asyncio
async def test_fast_reconnect_still_requires_fresh_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, runtime, _, database = await build_service(
        tmp_path,
        monkeypatch,
        {
            "consistent": True,
            "unmatched_local_active_count": 0,
            "unmatched_broker_active_count": 0,
        },
    )
    await service.start()
    runtime.reconcile_calls = 0
    version_before_disconnect = service.risk.version

    # The gateway reconnected before the watchdog observed connected=False.
    runtime.gateway.connected = True
    runtime.gateway.subscribed = True
    runtime.gateway.recovery_required = True
    await service._watchdog_once()

    assert runtime.reconcile_calls == 1
    assert runtime.gateway.recovery_required is False
    assert service.risk.state is RiskOperatingState.NORMAL
    assert service.risk.version > version_before_disconnect
    assert service.risk.reason == "gateway reconnected and reconciled"
    await service.stop()
    await database.dispose()
