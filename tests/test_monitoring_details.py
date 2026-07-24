from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete

from dyadtetrade.api.routes.monitoring import (
    broker_trades,
    delivery_statements,
    strategy_monitor,
    strategy_runtime_detail,
)
from dyadtetrade.db.models import (
    BrokerSettlementRecord,
    OmsOrderRecord,
    OmsTradeRecord,
    RiskEventRecord,
    StrategyRunRecord,
)
from dyadtetrade.db.session import SessionFactory, initialize_database


@pytest.mark.asyncio
async def test_delivery_statement_masks_account_and_calculates_settlement() -> None:
    await initialize_database()
    suffix = uuid4().hex
    trade_id = f"trade-{suffix}"
    strategy_id = f"strategy-{suffix}"
    now = datetime.now(UTC).replace(tzinfo=None)
    async with SessionFactory() as session:
        session.add(
            BrokerSettlementRecord(
                settlement_id=f"settlement-{suffix}",
                broker_trade_id=trade_id,
                broker_order_id=f"order-{suffix}",
                account_id="sim-account-12345678",
                symbol="600519.SH",
                direction="buy",
                quantity=100,
                price=10,
                notional=1000,
                commission=5,
                tax=0,
                transfer_fee=0,
                settlement_amount=-1005,
                execution_mode="qmt_sim",
                strategy_id=strategy_id,
                is_simulated=True,
                source="broker_settlement_query",
                broker_reported_at=now,
            )
        )
        await session.commit()
    try:
        payload = await delivery_statements(
            start_date=now.date().isoformat(),
            end_date=now.date().isoformat(),
            symbol="600519.SH",
            strategy_id=strategy_id,
            limit=100,
        )
        records = payload["records"]
        assert isinstance(records, list) and len(records) == 1
        assert records[0]["account"] == "***5678"
        assert records[0]["settlement_amount"] == "-1005.00000000"
        summary = payload["summary"]
        assert isinstance(summary, dict)
        assert summary["total_fees"] == "5.00000000"
    finally:
        async with SessionFactory() as session:
            await session.execute(
                delete(BrokerSettlementRecord).where(
                    BrokerSettlementRecord.broker_trade_id == trade_id
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_paper_trade_never_appears_in_broker_settlement_page() -> None:
    await initialize_database()
    suffix = uuid4().hex
    trade_id = f"paper-{suffix}"
    now = datetime.now(UTC).replace(tzinfo=None)
    async with SessionFactory() as session:
        session.add(
            OmsTradeRecord(
                trade_id=trade_id,
                order_id=f"paper-order-{suffix}",
                account_id="paper",
                symbol="600519.SH",
                direction="buy",
                quantity=100,
                price=10,
                fees=5,
                execution_mode="paper",
                source="paper_match",
                is_simulated=True,
                timestamp=now,
            )
        )
        await session.commit()
    try:
        payload = await delivery_statements(
            start_date=now.date().isoformat(),
            end_date=now.date().isoformat(),
            symbol="600519.SH",
            strategy_id=None,
            limit=100,
        )
        assert all(record.get("trade_id") != trade_id for record in payload["records"])
    finally:
        async with SessionFactory() as session:
            await session.execute(delete(OmsTradeRecord).where(OmsTradeRecord.trade_id == trade_id))
            await session.commit()


@pytest.mark.asyncio
async def test_broker_trade_api_preserves_aggregate_fee_semantics() -> None:
    await initialize_database()
    suffix = uuid4().hex
    trade_id = f"broker-fee-{suffix}"
    now = datetime.now(UTC).replace(tzinfo=None)
    async with SessionFactory() as session:
        session.add(
            OmsTradeRecord(
                trade_id=trade_id,
                order_id=f"broker-order-{suffix}",
                account_id="sim-account",
                symbol="510300.SH",
                direction="buy",
                quantity=100,
                price=4.2,
                fees=1.25,
                commission=0,
                tax=0,
                transfer_fee=0,
                broker_reported_fee=1.25,
                fee_source="xttrade.commission",
                fee_breakdown_complete=False,
                execution_mode="qmt_sim",
                source="qmt_trade_query",
                data_origin="qmt_broker",
                is_simulated=True,
                broker_order_id=f"broker-order-{suffix}",
                broker_trade_id=trade_id,
                broker_reported_at=now,
                timestamp=now,
            )
        )
        await session.commit()
    try:
        payload = await broker_trades(limit=500)
        record = next(item for item in payload["records"] if item["trade_id"] == trade_id)
        assert record["fees"] == "1.25000000"
        assert record["broker_reported_fee"] == "1.25000000"
        assert record["fee_source"] == "xttrade.commission"
        assert record["fee_breakdown_complete"] is False
        assert record["data_origin"] == "qmt_broker"
    finally:
        async with SessionFactory() as session:
            await session.execute(delete(OmsTradeRecord).where(OmsTradeRecord.trade_id == trade_id))
            await session.commit()


@pytest.mark.asyncio
async def test_strategy_runtime_detail_never_grants_live_order_capability() -> None:
    await initialize_database()
    suffix = uuid4().hex
    strategy_id = f"runtime-{suffix}"
    async with SessionFactory() as session:
        session.add(
            StrategyRunRecord(
                strategy_instance_id=strategy_id,
                strategy_name="isolated_test_strategy",
                strategy_version="1.2.3",
                mode="paper",
                status="running",
                parameters_json='{"lookback": 20}',
                state_json='{"selected": ["600519.SH"]}',
                last_heartbeat=datetime.now(UTC).replace(tzinfo=None),
                started_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()
    try:
        detail = await strategy_runtime_detail(strategy_id)
        assert detail["health"] == "healthy"
        assert detail["parameters"] == {"lookback": 20}
        assert detail["can_send_live_orders"] is False
        assert detail["orders"] == []
    finally:
        async with SessionFactory() as session:
            await session.execute(
                delete(StrategyRunRecord).where(
                    StrategyRunRecord.strategy_instance_id == strategy_id
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_strategy_monitor_aggregates_multiple_runtime_states() -> None:
    await initialize_database()
    suffix = uuid4().hex
    healthy_id = f"healthy-{suffix}"
    failed_id = f"failed-{suffix}"
    now = datetime.now(UTC).replace(tzinfo=None)
    async with SessionFactory() as session:
        session.add_all(
            [
                StrategyRunRecord(
                    strategy_instance_id=healthy_id,
                    strategy_name="healthy_strategy",
                    strategy_version="1.0.0",
                    mode="paper",
                    status="running",
                    parameters_json='{"lookback": 5}',
                    state_json='{"phase": "open"}',
                    last_heartbeat=now,
                    started_at=now,
                ),
                StrategyRunRecord(
                    strategy_instance_id=failed_id,
                    strategy_name="failed_strategy",
                    strategy_version="2.0.0",
                    mode="paper",
                    status="failed",
                    error_message="isolated test failure",
                    started_at=now,
                    stopped_at=now,
                ),
                OmsOrderRecord(
                    order_id=f"order-{suffix}",
                    client_order_id=f"client-{suffix}",
                    idempotency_key=f"key-{suffix}",
                    account_id="paper",
                    strategy_id=healthy_id,
                    symbol="600519.SH",
                    direction="buy",
                    quantity=100,
                    status="submitted",
                    execution_mode="paper",
                ),
                RiskEventRecord(
                    account_id="paper",
                    strategy_id=healthy_id,
                    order_id=f"order-{suffix}",
                    level="warning",
                    code="TEST_RISK",
                    message="isolated monitor test",
                ),
            ]
        )
        await session.commit()
    try:
        payload = await strategy_monitor()
        rows = {
            str(row["strategy_id"]): row for row in payload["strategies"] if isinstance(row, dict)
        }
        assert rows[healthy_id]["health"] == "healthy"
        assert rows[healthy_id]["pending_order_count"] == 1
        assert rows[healthy_id]["risk_event_count"] == 1
        assert rows[healthy_id]["can_send_live_orders"] is False
        assert rows[failed_id]["health"] == "error"
        assert payload["mode_counts"]["paper"] >= 2
        assert payload["error_count"] >= 1
    finally:
        async with SessionFactory() as session:
            await session.execute(
                delete(RiskEventRecord).where(RiskEventRecord.strategy_id == healthy_id)
            )
            await session.execute(
                delete(OmsOrderRecord).where(OmsOrderRecord.strategy_id == healthy_id)
            )
            await session.execute(
                delete(StrategyRunRecord).where(
                    StrategyRunRecord.strategy_instance_id.in_((healthy_id, failed_id))
                )
            )
            await session.commit()
