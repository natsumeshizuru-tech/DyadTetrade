from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.core.config import ExecutionMode
from dyadtetrade.db.models import (
    AuditEventRecord,
    Base,
    OmsOrderRecord,
    OmsTradeRecord,
    PositionRecord,
)
from dyadtetrade.domain import Direction, OrderRequest, Tick
from dyadtetrade.integrations.qmt_sim_gateway import QmtSimTradingGateway, SingleProcessLock
from dyadtetrade.modules.execution import service as execution_service
from dyadtetrade.modules.execution.service import QmtSimRuntime
from tests.test_qmt_sim_gateway import (
    TEST_QMT_ORDER_CONSTANTS,
    FakeQmtClient,
    request,
    settings,
    tick,
)


@pytest.mark.asyncio
async def test_qmt_runtime_persists_submission_callbacks_and_restart_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    client = FakeQmtClient()
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "runtime.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()
    assert runtime.snapshot()["connected"] is True
    gateway.update_market(
        Tick(
            symbol="600519.SH",
            timestamp=datetime.now(UTC),
            last_price=Decimal("10"),
            volume=1000,
            amount=Decimal("10000"),
        )
    )
    submission = gateway.submit_async(
        OrderRequest(
            symbol="600519.SH",
            direction=Direction.BUY,
            quantity=100,
            limit_price=Decimal("10"),
            strategy_id="runtime-test",
            account_id=active.qmt_account_id,
            client_order_id="runtime-client",
            idempotency_key="runtime-key",
            strategy_release_id="strategy-release-runtime",
            signal_id="signal-runtime",
        )
    )
    await runtime.persist_submission(submission)
    gateway.on_async_order_response({"seq": 7, "order_id": 9001})
    gateway.on_order(
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "order_sysid": "SYS-RUNTIME",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 100,
            "traded_price": 10,
            "order_status": 56,
            "order_remark": "runtime-client",
        }
    )
    gateway.on_trade(
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "traded_id": "runtime-trade",
            "order_type": 23,
            "traded_volume": 100,
            "traded_price": 10,
            "traded_time": 1_767_225_600,
        }
    )
    await runtime.persist_snapshot()
    gateway.safety.restore_daily_usage(
        trading_day=datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai")).date(),
        notional=Decimal("0"),
        idempotency_keys=set(),
        order_timestamps=[],
    )
    await runtime.restore_safety_usage()
    restored = gateway.safety.check(
        OrderRequest(
            symbol="600519.SH",
            direction=Direction.BUY,
            quantity=100,
            limit_price=Decimal("10"),
            strategy_id="runtime-test",
            account_id=active.qmt_account_id,
            client_order_id="runtime-client-duplicate",
            idempotency_key="runtime-key",
        ),
        account=gateway.account,
        tick=gateway.market["600519.SH"],
        connected=True,
        subscribed=True,
        account_healthy=True,
        now=datetime.now(UTC),
    )
    assert restored.code == "DUPLICATE_ORDER"
    async with sessions() as session:
        order = await session.scalar(
            select(OmsOrderRecord).where(OmsOrderRecord.client_order_id == "runtime-client")
        )
        assert order is not None
        assert order.qmt_seq == 7 and order.qmt_order_id == 9001
        assert order.order_sysid == "SYS-RUNTIME"
        assert order.execution_mode == ExecutionMode.QMT_SIM.value
        assert order.strategy_release_id == "strategy-release-runtime"
        assert order.signal_id == "signal-runtime"
        assert await session.scalar(select(func.count()).select_from(OmsTradeRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(PositionRecord)) == 0
    await runtime.stop()
    assert runtime.snapshot()["connected"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_qmt_reconciliation_imports_external_order_and_links_broker_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    client = FakeQmtClient()
    client.orders = [
        {
            "stock_code": "600519.SH",
            "order_id": 0,
            "order_sysid": "9101",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 100,
            "traded_price": 10,
            "price": 10,
            "order_status": 51,
            "order_time": 1_767_225_590,
            "strategy_name": "external-strategy",
        }
    ]
    client.trades = [
        {
            "stock_code": "600519.SH",
            "order_id": 0,
            "order_sysid": "9101",
            "traded_id": "external-trade-1",
            "order_type": 23,
            "traded_volume": 100,
            "traded_price": 10,
            "traded_time": 1_767_225_600,
            "strategy_name": "external-strategy",
            "commission": 1.234567891,
        }
    ]
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "external-runtime.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()

    async with sessions() as session:
        order = await session.scalar(
            select(OmsOrderRecord).where(OmsOrderRecord.order_sysid == "9101")
        )
        trade = await session.scalar(
            select(OmsTradeRecord).where(OmsTradeRecord.broker_trade_id == "external-trade-1")
        )
        assert order is not None and trade is not None
        assert order.source == "qmt_order_query"
        assert order.data_origin == "qmt_broker"
        assert order.is_simulated is True
        assert order.broker_reported_at is not None
        assert order.qmt_order_id is None
        assert trade.broker_order_id == "9101"
        assert trade.qmt_order_id is None
        assert trade.order_sysid == "9101"
        assert trade.order_id == order.order_id
        assert order.qmt_order_id is None
        assert order.order_sysid == "9101"
        assert trade.fees == Decimal("1.23456789")
        assert trade.broker_reported_fee == Decimal("1.23456789")
        assert trade.commission == Decimal("0")
        assert trade.tax == Decimal("0")
        assert trade.transfer_fee == Decimal("0")
        assert trade.fee_source == "xttrade.commission"
        assert trade.fee_breakdown_complete is False
        assert trade.data_origin == "qmt_broker"

    client.trades[0]["commission"] = 1.5  # type: ignore[index]
    await runtime.reconcile()

    async with sessions() as session:
        trade = await session.scalar(
            select(OmsTradeRecord).where(OmsTradeRecord.broker_trade_id == "external-trade-1")
        )
        assert trade is not None
        assert trade.fees == Decimal("1.50000000")
        assert trade.broker_reported_fee == Decimal("1.50000000")
        fee_audit = await session.scalar(
            select(AuditEventRecord).where(AuditEventRecord.event_type == "qmt_trade_fee_refreshed")
        )
        assert fee_audit is not None

    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_broker_order_is_reused_when_account_moves_from_qmt_sim_to_qmt_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    broker_order = {
        "stock_code": "600519.SH",
        "order_id": 0,
        "order_sysid": "CROSS-MODE-ORDER",
        "order_type": 23,
        "order_volume": 100,
        "traded_volume": 100,
        "traded_price": 10,
        "price": 10,
        "order_status": 56,
        "order_time": 1_767_225_590,
        "strategy_name": "external-strategy",
    }

    sim_settings = settings(tmp_path)
    sim_client = FakeQmtClient()
    sim_client.orders = [broker_order.copy()]
    sim_gateway = QmtSimTradingGateway(
        settings=sim_settings,
        client=sim_client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "cross-mode-sim.lock"),
    )
    sim_runtime = QmtSimRuntime(sim_settings, sim_gateway)
    await sim_runtime.start()
    await sim_runtime.stop()

    live_settings = settings(
        tmp_path,
        execution_mode=ExecutionMode.QMT_LIVE,
        enable_qmt_sim_trading=False,
        enable_live_trading=True,
        enable_qmt_live_trading=True,
        qmt_live_account_whitelist=[sim_settings.qmt_account_id],
        qmt_live_confirmation="I_CONFIRM_QMT_LIVE",
        qmt_live_release_date=date.today().isoformat(),
        qmt_account_is_simulated=True,
    )
    live_client = FakeQmtClient()
    live_client.orders = [broker_order.copy()]
    live_gateway = QmtSimTradingGateway(
        settings=live_settings,
        client=live_client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "cross-mode-live.lock"),
    )
    live_runtime = QmtSimRuntime(live_settings, live_gateway)
    await live_runtime.start()

    async with sessions() as session:
        orders = (await session.scalars(select(OmsOrderRecord))).all()
        assert len(orders) == 1
        assert orders[0].order_sysid == "CROSS-MODE-ORDER"
        assert orders[0].execution_mode == ExecutionMode.QMT_SIM.value
        assert orders[0].is_simulated is True

    await live_runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_blocks_on_local_active_order_missing_from_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    async with sessions() as session:
        session.add(
            OmsOrderRecord(
                order_id="local-active-missing",
                client_order_id="local-active-missing",
                idempotency_key="local-active-missing-key",
                account_id=active.qmt_account_id,
                strategy_id="restart-test",
                symbol="600519.SH",
                direction="buy",
                quantity=100,
                limit_price=Decimal("10"),
                status="SUBMITTED",
                execution_mode="qmt_sim",
                qmt_order_id=8123,
                source="qmt_order_submit",
                data_origin="dyadtetrade",
                is_simulated=True,
                payload_json='{"limit_price":"10"}',
            )
        )
        await session.commit()

    client = FakeQmtClient()
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "missing-active.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()

    report = runtime.last_reconciliation
    assert report is not None and report["consistent"] is False
    assert report["unmatched_local_active_count"] == 1
    assert report["unmatched_local_active_order_ids"] == ["local-active-missing"]
    assert runtime.last_reconciled_at is None
    assert runtime.last_error is not None and runtime.last_error.startswith(
        "reconciliation mismatch"
    )
    assert str(runtime.snapshot()["last_error"]).startswith("reconciliation mismatch")

    gateway.recovery_required = True
    await runtime.reconcile()
    assert gateway.recovery_required is True
    async with sessions() as session:
        mismatch_events = (
            await session.scalars(
                select(AuditEventRecord).where(
                    AuditEventRecord.event_type == "qmt_reconciliation_mismatch"
                )
            )
        ).all()
        assert len(mismatch_events) == 1

    client.orders = [
        {
            "stock_code": "600519.SH",
            "order_id": 8123,
            "order_sysid": "SYS-8123",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 0,
            "traded_price": 0,
            "price": 10,
            "order_status": 50,
            "order_time": 1_767_225_590,
            "order_remark": "local-active-missing",
        }
    ]
    result = await runtime.reconcile()
    consistency = result["ledger_consistency"]
    assert isinstance(consistency, dict) and consistency["consistent"] is True
    assert runtime.last_reconciled_at is not None
    assert runtime.last_error is None
    assert gateway.recovery_required is False

    # A later broker snapshot that drops the active order must not be hidden by
    # the gateway's older in-memory callback state. It is a new audit incident.
    client.orders = []
    repeated = await runtime.reconcile()
    repeated_consistency = repeated["ledger_consistency"]
    assert isinstance(repeated_consistency, dict)
    assert repeated_consistency["consistent"] is False
    assert repeated_consistency["unmatched_local_active_count"] == 1
    async with sessions() as session:
        mismatch_events = (
            await session.scalars(
                select(AuditEventRecord).where(
                    AuditEventRecord.event_type == "qmt_reconciliation_mismatch"
                )
            )
        ).all()
        assert len(mismatch_events) == 2

    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_refreshes_known_order_missing_from_today_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    async with sessions() as session:
        session.add(
            OmsOrderRecord(
                order_id="known-prior-order",
                client_order_id="known-prior-order",
                idempotency_key="known-prior-order-key",
                account_id=active.qmt_account_id,
                strategy_id="external-broker",
                symbol="600519.SH",
                direction="sell",
                quantity=100,
                limit_price=Decimal("10"),
                status="SUBMITTED",
                execution_mode="qmt_sim",
                qmt_order_id=8123,
                order_sysid="SYS-8123",
                source="qmt_order_query",
                data_origin="qmt_broker",
                is_simulated=True,
                payload_json='{"external_order":true}',
            )
        )
        await session.commit()

    client = FakeQmtClient()
    client.orders_by_id[8123] = {
        "stock_code": "600519.SH",
        "order_id": 8123,
        "order_sysid": "SYS-8123",
        "order_type": 24,
        "order_volume": 100,
        "traded_volume": 0,
        "traded_price": 0,
        "price": 10,
        "order_status": 53,
        "order_time": 1_767_225_590,
    }
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "known-prior-order.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()

    assert runtime.last_reconciliation is not None
    assert runtime.last_reconciliation["consistent"] is True
    async with sessions() as session:
        row = await session.scalar(
            select(OmsOrderRecord).where(OmsOrderRecord.order_id == "known-prior-order")
        )
        assert row is not None
        assert row.status == "CANCELLED"

    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_restart_recovers_crash_window_by_client_order_id_without_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    async with sessions() as session:
        session.add(
            OmsOrderRecord(
                order_id="qmt-seq-7",
                client_order_id="crash-window-client",
                idempotency_key="crash-window-key",
                account_id=active.qmt_account_id,
                strategy_id="restart-test",
                symbol="600519.SH",
                direction="buy",
                quantity=100,
                limit_price=Decimal("10"),
                status="SUBMITTING",
                execution_mode="qmt_sim",
                qmt_seq=7,
                source="qmt_order_submit",
                data_origin="dyadtetrade",
                is_simulated=True,
                payload_json='{"limit_price":"10"}',
            )
        )
        await session.commit()

    client = FakeQmtClient()
    client.orders = [
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "order_sysid": "SYS-9001",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 0,
            "traded_price": 0,
            "price": 10,
            "order_status": 50,
            "order_time": 1_767_225_590,
            "order_remark": "crash-window-client",
        }
    ]
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "crash-window.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()

    assert runtime.last_reconciliation is not None
    assert runtime.last_reconciliation["consistent"] is True
    async with sessions() as session:
        orders = (await session.scalars(select(OmsOrderRecord))).all()
        assert len(orders) == 1
        assert orders[0].order_id == "qmt-seq-7"
        assert orders[0].qmt_order_id == 9001
        assert orders[0].order_sysid == "SYS-9001"
        assert orders[0].status == "SUBMITTED"

    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_trade_before_order_callback_persists_monotonic_order_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    client = FakeQmtClient()
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "trade-before-order.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()
    gateway.update_market(tick())
    submission = gateway.submit_async(request())
    await runtime.persist_submission(submission)
    gateway.on_async_order_response({"seq": 7, "order_id": 9001, "error_msg": ""})
    gateway.on_trade(
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "order_sysid": "SYS-9001",
            "traded_id": "trade-before-order-runtime",
            "order_type": 23,
            "traded_volume": 40,
            "traded_price": 10,
            "traded_time": 1_767_225_600,
        }
    )
    await runtime.persist_snapshot()

    async with sessions() as session:
        row = await session.scalar(select(OmsOrderRecord))
        assert row is not None
        assert row.status == "PARTIALLY_FILLED"
        assert row.filled_quantity == 40
        assert row.qmt_order_id == 9001

    gateway.on_order(
        {
            "stock_code": "600519.SH",
            "order_id": 9001,
            "order_sysid": "SYS-9001",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 0,
            "traded_price": 0,
            "price": 10,
            "order_status": 50,
            "order_remark": "runtime-client",
        }
    )
    await runtime.persist_snapshot()
    async with sessions() as session:
        row = await session.scalar(select(OmsOrderRecord))
        assert row is not None
        assert row.status == "PARTIALLY_FILLED"
        assert row.filled_quantity == 40

    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_broker_query_cannot_regress_persisted_terminal_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    async with sessions() as session:
        session.add(
            OmsOrderRecord(
                order_id="persisted-filled",
                client_order_id="persisted-filled-client",
                idempotency_key="persisted-filled-key",
                account_id=active.qmt_account_id,
                strategy_id="restart-test",
                symbol="600519.SH",
                direction="buy",
                quantity=100,
                filled_quantity=100,
                limit_price=Decimal("10"),
                average_fill_price=Decimal("10"),
                status="FILLED",
                execution_mode="qmt_sim",
                qmt_order_id=8123,
                source="qmt_order_callback",
                data_origin="qmt_broker",
                is_simulated=True,
                payload_json='{"limit_price":"10"}',
            )
        )
        await session.commit()

    client = FakeQmtClient()
    client.orders = [
        {
            "stock_code": "600519.SH",
            "order_id": 8123,
            "order_sysid": "SYS-8123",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 0,
            "traded_price": 0,
            "price": 10,
            "order_status": 50,
            "order_time": 1_767_225_590,
            "order_remark": "persisted-filled-client",
        }
    ]
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "stale-query.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()

    async with sessions() as session:
        row = await session.scalar(select(OmsOrderRecord))
        assert row is not None
        assert row.status == "FILLED"
        assert row.filled_quantity == 100
        regression = await session.scalar(
            select(AuditEventRecord).where(
                AuditEventRecord.event_type == "qmt_order_state_regression"
            )
        )
        assert regression is not None
    assert runtime.last_reconciliation is not None
    assert runtime.last_reconciliation["consistent"] is False
    assert runtime.last_reconciliation["unmatched_broker_active_count"] == 1
    assert runtime.last_reconciled_at is None

    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_requires_healthy_account_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    client = FakeQmtClient()
    client.asset = None
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "unhealthy-account.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()

    report = runtime.last_reconciliation
    assert report is not None
    assert report["consistent"] is False
    assert report["gateway_health_consistent"] is False
    assert report["account_healthy"] is False
    assert runtime.last_reconciled_at is None

    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_upgrades_numeric_identity_by_existing_order_sysid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(execution_service, "SessionFactory", sessions)
    active = settings(tmp_path)
    async with sessions() as session:
        session.add(
            OmsOrderRecord(
                order_id="sysid-only-order",
                client_order_id="sysid-only-client",
                idempotency_key="sysid-only-key",
                account_id=active.qmt_account_id,
                strategy_id="external-broker",
                symbol="600519.SH",
                direction="buy",
                quantity=100,
                filled_quantity=100,
                limit_price=Decimal("10"),
                average_fill_price=Decimal("10"),
                status="FILLED",
                execution_mode="qmt_sim",
                qmt_order_id=None,
                order_sysid="SYS-UPGRADE",
                source="qmt_order_query",
                data_origin="qmt_broker",
                is_simulated=True,
                payload_json='{"external_order":true}',
            )
        )
        await session.commit()

    client = FakeQmtClient()
    client.orders = [
        {
            "stock_code": "600519.SH",
            "order_id": 9901,
            "order_sysid": "SYS-UPGRADE",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 100,
            "traded_price": 10,
            "price": 10,
            "order_status": 56,
            "order_time": 1_767_225_590,
            "order_remark": "",
        }
    ]
    gateway = QmtSimTradingGateway(
        settings=active,
        client=client,
        stock_account=object(),
        order_constants=TEST_QMT_ORDER_CONSTANTS,
        process_lock=SingleProcessLock(tmp_path / "identity-upgrade.lock"),
    )
    runtime = QmtSimRuntime(active, gateway)
    await runtime.start()

    async with sessions() as session:
        rows = (await session.scalars(select(OmsOrderRecord))).all()
        assert len(rows) == 1
        assert rows[0].order_id == "sysid-only-order"
        assert rows[0].qmt_order_id == 9901
        assert rows[0].order_sysid == "SYS-UPGRADE"
    assert runtime.last_reconciliation is not None
    assert runtime.last_reconciliation["consistent"] is True

    await runtime.stop()
    await engine.dispose()
