from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, desc, func, select

from dyadtetrade.api.routes.paper_trading import paper_runners, paper_service
from dyadtetrade.api.routes.realtime import realtime_service
from dyadtetrade.core.config import get_settings
from dyadtetrade.db.models import (
    AuditEventRecord,
    BrokerSettlementRecord,
    OmsOrderRecord,
    OmsTradeRecord,
    RiskEventRecord,
    StrategyRunRecord,
    TradingDay,
)
from dyadtetrade.db.session import SessionFactory
from dyadtetrade.integrations.xtquant import XtQuantGateway, get_xtquant_status
from dyadtetrade.modules.monitoring import (
    account_monitor_snapshot,
    market_operation_state,
    strategy_health,
    strategy_recovery_status,
    strategy_runtime_seconds,
)
from dyadtetrade.modules.monitoring.market_session import CHINA_TZ
from dyadtetrade.modules.realtime import xtdata_subscription_service
from dyadtetrade.trading.client import trading_engine_client

router = APIRouter(tags=["monitoring"])


def _masked_account(value: str) -> str:
    return f"***{value[-4:]}" if value else "unconfigured"


def _monitor_int(value: object) -> int:
    if isinstance(value, (bool, int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _utc_activity_key(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _runtime_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None and value.year >= 2000 else None


def _parse_day(value: str, *, end_of_day: bool = False) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="date must use YYYY-MM-DD") from exc
    return (
        parsed.replace(hour=23, minute=59, second=59, microsecond=999999) if end_of_day else parsed
    )


async def _delivery_statement_data(
    *,
    start_date: str,
    end_date: str,
    symbol: str | None,
    strategy_id: str | None,
    limit: int,
) -> dict[str, object]:
    start = _parse_day(start_date)
    end = _parse_day(end_date, end_of_day=True)
    if end < start:
        raise HTTPException(status_code=422, detail="end_date must not precede start_date")
    filters = [
        BrokerSettlementRecord.broker_reported_at >= start,
        BrokerSettlementRecord.broker_reported_at <= end,
        BrokerSettlementRecord.execution_mode.in_(("qmt_sim", "qmt_live")),
        BrokerSettlementRecord.source.in_(("broker_settlement_query", "broker_settlement_import")),
    ]
    if symbol:
        filters.append(BrokerSettlementRecord.symbol == symbol.upper())
    if strategy_id:
        filters.append(BrokerSettlementRecord.strategy_id == strategy_id)
    statement = (
        select(BrokerSettlementRecord)
        .where(and_(*filters))
        .order_by(desc(BrokerSettlementRecord.broker_reported_at))
        .limit(limit)
    )
    async with SessionFactory() as session:
        rows = (await session.scalars(statement)).all()
    records: list[dict[str, object]] = []
    total_notional = Decimal("0")
    total_fees = Decimal("0")
    net_settlement = Decimal("0")
    for settlement in rows:
        fees = settlement.commission + settlement.tax + settlement.transfer_fee
        total_notional += settlement.notional
        total_fees += fees
        net_settlement += settlement.settlement_amount
        records.append(
            {
                "settlement_id": settlement.settlement_id,
                "trade_id": settlement.broker_trade_id,
                "order_id": settlement.broker_order_id,
                "client_order_id": None,
                "trade_date": settlement.broker_reported_at.date().isoformat(),
                "timestamp": settlement.broker_reported_at.replace(tzinfo=UTC).isoformat(),
                "symbol": settlement.symbol,
                "direction": settlement.direction,
                "business_type": "证券买入"
                if settlement.direction == "buy"
                else "证券卖出"
                if settlement.direction == "sell"
                else "成交",
                "quantity": settlement.quantity,
                "price": str(settlement.price),
                "notional": str(settlement.notional),
                "fees": str(fees),
                "commission": str(settlement.commission),
                "tax": str(settlement.tax),
                "transfer_fee": str(settlement.transfer_fee),
                "settlement_amount": str(settlement.settlement_amount),
                "strategy_id": settlement.strategy_id,
                "execution_mode": settlement.execution_mode,
                "account": _masked_account(settlement.account_id),
                "order_sysid": None,
            }
        )
    return {
        "start_date": start_date,
        "end_date": end_date,
        "records": records,
        "summary": {
            "trade_count": len(records),
            "total_notional": str(total_notional),
            "total_fees": str(total_fees),
            "net_settlement": str(net_settlement),
        },
        "source": "broker_settlements_only",
    }


@router.get("/monitoring/broker-trades")
async def broker_trades(limit: int = Query(default=500, ge=1, le=2000)) -> dict[str, object]:
    async with SessionFactory() as session:
        rows = (
            await session.scalars(
                select(OmsTradeRecord)
                .where(
                    OmsTradeRecord.execution_mode.in_(("qmt_sim", "qmt_live")),
                    OmsTradeRecord.source.in_(("qmt_trade_callback", "qmt_trade_query")),
                    OmsTradeRecord.broker_trade_id.is_not(None),
                )
                .order_by(desc(OmsTradeRecord.broker_reported_at))
                .limit(limit)
            )
        ).all()
    return {
        "records": [
            {
                "trade_id": row.trade_id,
                "broker_trade_id": row.broker_trade_id,
                "broker_order_id": row.broker_order_id,
                "qmt_order_id": row.qmt_order_id,
                "order_sysid": row.order_sysid,
                "symbol": row.symbol,
                "direction": row.direction,
                "quantity": row.quantity,
                "price": str(row.price),
                "fees": str(row.fees),
                "broker_reported_fee": (
                    str(row.broker_reported_fee) if row.broker_reported_fee is not None else None
                ),
                "fee_source": row.fee_source,
                "fee_breakdown_complete": row.fee_breakdown_complete,
                "execution_mode": row.execution_mode,
                "source": row.source,
                "data_origin": row.data_origin,
                "is_simulated": row.is_simulated,
                "broker_reported_at": row.broker_reported_at.isoformat()
                if row.broker_reported_at
                else None,
            }
            for row in rows
        ],
        "source": "qmt_broker_fact_only",
    }


@router.get("/monitoring/market-session")
async def market_session() -> dict[str, object]:
    observed = datetime.now(CHINA_TZ)
    trade_date = observed.strftime("%Y%m%d")
    calendar_source = "weekday_fallback"
    is_open: bool | None = None
    async with SessionFactory() as session:
        bounds = (
            await session.execute(
                select(func.min(TradingDay.trade_date), func.max(TradingDay.trade_date)).where(
                    TradingDay.market == "SH"
                )
            )
        ).one()
        if bounds[0] is not None and bounds[0] <= trade_date <= bounds[1]:
            is_open = bool(
                await session.scalar(
                    select(func.count())
                    .select_from(TradingDay)
                    .where(
                        TradingDay.market == "SH",
                        TradingDay.trade_date == trade_date,
                    )
                )
            )
            calendar_source = "sqlite_xtquant"
    if is_open is None:
        try:
            days = await asyncio.to_thread(
                XtQuantGateway().trading_days, trade_date, trade_date, "SH"
            )
            is_open = trade_date in days
            calendar_source = "xtquant"
        except Exception:
            is_open = None
    return market_operation_state(
        observed,
        is_trading_day=is_open,
        calendar_source=calendar_source,
    ).snapshot()


@router.get("/monitoring/strategies")
async def strategy_monitor() -> dict[str, object]:
    async with SessionFactory() as session:
        runs = (
            await session.scalars(
                select(StrategyRunRecord).order_by(desc(StrategyRunRecord.updated_at))
            )
        ).all()
        order_stats = (
            await session.execute(
                select(
                    OmsOrderRecord.strategy_id,
                    func.count(OmsOrderRecord.id),
                    func.sum(
                        OmsOrderRecord.status.in_(
                            ("created", "submitting", "submitted", "partially_filled", "cancelling")
                        )
                    ),
                    func.max(OmsOrderRecord.updated_at),
                ).group_by(OmsOrderRecord.strategy_id)
            )
        ).all()
        trade_stats = (
            await session.execute(
                select(
                    OmsTradeRecord.strategy_id,
                    func.count(OmsTradeRecord.id),
                    func.max(OmsTradeRecord.timestamp),
                )
                .where(OmsTradeRecord.strategy_id.is_not(None))
                .group_by(OmsTradeRecord.strategy_id)
            )
        ).all()
        risk_stats = (
            await session.execute(
                select(
                    RiskEventRecord.strategy_id,
                    func.count(RiskEventRecord.id),
                    func.max(RiskEventRecord.created_at),
                )
                .where(RiskEventRecord.strategy_id.is_not(None))
                .group_by(RiskEventRecord.strategy_id)
            )
        ).all()
    orders_by_strategy = {
        str(strategy_id): (int(count), int(pending or 0), updated_at)
        for strategy_id, count, pending, updated_at in order_stats
    }
    trades_by_strategy = {
        str(strategy_id): (int(count), updated_at) for strategy_id, count, updated_at in trade_stats
    }
    risks_by_strategy = {
        str(strategy_id): (int(count), updated_at) for strategy_id, count, updated_at in risk_stats
    }
    active = {item.strategy.strategy_id: item.status() for item in paper_runners.values()}
    strategy_rows: list[dict[str, object]] = []
    for run_row in runs:
        live = active.get(run_row.strategy_instance_id)
        running = bool(live.get("running")) if live is not None else run_row.status == "running"
        status = "running" if running else run_row.status
        heartbeat = (
            datetime.fromisoformat(str(live["last_heartbeat"]))
            if live is not None and live.get("last_heartbeat")
            else run_row.last_heartbeat
        )
        health = strategy_health(
            heartbeat,
            status=status,
            error=run_row.error_message,
        )
        persisted_orders, pending_orders, order_updated_at = orders_by_strategy.get(
            run_row.strategy_instance_id, (0, 0, None)
        )
        persisted_trades, trade_updated_at = trades_by_strategy.get(
            run_row.strategy_instance_id, (0, None)
        )
        risk_count, risk_updated_at = risks_by_strategy.get(run_row.strategy_instance_id, (0, None))
        activity_candidates = [
            value
            for value in (heartbeat, order_updated_at, trade_updated_at, risk_updated_at)
            if value
        ]
        last_activity_at = (
            max(activity_candidates, key=_utc_activity_key) if activity_candidates else None
        )
        strategy_rows.append(
            {
                "strategy_id": run_row.strategy_instance_id,
                "name": run_row.strategy_name,
                "version": run_row.strategy_version,
                "mode": run_row.mode,
                "status": status,
                "health": health.health,
                "heartbeat_age_seconds": health.heartbeat_age_seconds,
                "last_heartbeat": heartbeat.isoformat() if heartbeat else None,
                "started_at": _runtime_timestamp(run_row.started_at),
                "stopped_at": _runtime_timestamp(run_row.stopped_at),
                "runtime_seconds": strategy_runtime_seconds(
                    run_row.started_at,
                    stopped_at=run_row.stopped_at if not running else None,
                ),
                "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
                "parameters": live.get("parameters", {})
                if live is not None
                else json.loads(run_row.parameters_json or "{}"),
                "state": live.get("state", {})
                if live is not None
                else json.loads(run_row.state_json or "{}"),
                "order_count": _monitor_int(live.get("order_count", persisted_orders))
                if live is not None
                else persisted_orders,
                "pending_order_count": pending_orders,
                "trade_count": _monitor_int(live.get("trade_count", persisted_trades))
                if live is not None
                else persisted_trades,
                "risk_event_count": risk_count,
                "record_count": live.get("record_count", 0) if live is not None else 0,
                "error": run_row.error_message,
                "process_role": "paper-strategy-runner"
                if run_row.mode == "paper"
                else "strategy-runtime",
                "recovery_status": strategy_recovery_status(
                    status=status,
                    attached=live is not None,
                ),
                "live_order_policy": "blocked",
                "can_send_live_orders": False,
            }
        )
    mode_counts: dict[str, int] = {}
    for item in strategy_rows:
        mode = str(item["mode"])
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "total_count": len(strategy_rows),
        "running_count": sum(item["status"] == "running" for item in strategy_rows),
        "healthy_count": sum(item["health"] == "healthy" for item in strategy_rows),
        "stale_count": sum(item["health"] == "stale" for item in strategy_rows),
        "error_count": sum(item["health"] == "error" for item in strategy_rows),
        "stopped_count": sum(item["health"] == "stopped" for item in strategy_rows),
        "waiting_count": sum(item["health"] == "waiting" for item in strategy_rows),
        "mode_counts": mode_counts,
        "strategies": strategy_rows,
        "realtime": realtime_service.snapshot(),
        "release_gate": {
            "qmt_live_enabled": get_settings().enable_live_trading,
            "strategy_live_order_policy": "blocked",
            "required_path": "strategy -> order intent -> risk -> OMS -> gateway",
        },
    }


@router.get("/monitoring/strategies/{strategy_id}")
async def strategy_runtime_detail(strategy_id: str) -> dict[str, object]:
    async with SessionFactory() as session:
        run_row = await session.scalar(
            select(StrategyRunRecord).where(StrategyRunRecord.strategy_instance_id == strategy_id)
        )
        if run_row is None:
            raise HTTPException(status_code=404, detail="strategy run not found")
        orders = (
            await session.scalars(
                select(OmsOrderRecord)
                .where(OmsOrderRecord.strategy_id == strategy_id)
                .order_by(desc(OmsOrderRecord.updated_at))
                .limit(100)
            )
        ).all()
        trades = (
            await session.execute(
                select(OmsTradeRecord, OmsOrderRecord)
                .join(OmsOrderRecord, OmsOrderRecord.order_id == OmsTradeRecord.order_id)
                .where(OmsOrderRecord.strategy_id == strategy_id)
                .order_by(desc(OmsTradeRecord.timestamp))
                .limit(100)
            )
        ).all()
        risks = (
            await session.scalars(
                select(RiskEventRecord)
                .where(RiskEventRecord.strategy_id == strategy_id)
                .order_by(desc(RiskEventRecord.created_at))
                .limit(50)
            )
        ).all()
        audits = (
            await session.scalars(
                select(AuditEventRecord)
                .where(AuditEventRecord.object_id == strategy_id)
                .order_by(desc(AuditEventRecord.created_at))
                .limit(50)
            )
        ).all()
        total_order_count = int(
            await session.scalar(
                select(func.count(OmsOrderRecord.id)).where(
                    OmsOrderRecord.strategy_id == strategy_id
                )
            )
            or 0
        )
        pending_order_count = int(
            await session.scalar(
                select(func.count(OmsOrderRecord.id)).where(
                    OmsOrderRecord.strategy_id == strategy_id,
                    OmsOrderRecord.status.in_(
                        (
                            "created",
                            "submitting",
                            "submitted",
                            "partially_filled",
                            "cancelling",
                        )
                    ),
                )
            )
            or 0
        )
        total_trade_count = int(
            await session.scalar(
                select(func.count(OmsTradeRecord.id)).where(
                    OmsTradeRecord.strategy_id == strategy_id
                )
            )
            or 0
        )
        total_risk_count = int(
            await session.scalar(
                select(func.count(RiskEventRecord.id)).where(
                    RiskEventRecord.strategy_id == strategy_id
                )
            )
            or 0
        )
    active = paper_runners.get(strategy_id)
    active_status = active.status() if active is not None else None
    heartbeat = (
        str(active_status.get("last_heartbeat"))
        if active_status is not None and active_status.get("last_heartbeat")
        else run_row.last_heartbeat.isoformat()
        if run_row.last_heartbeat
        else None
    )
    running = (
        bool(active_status.get("running"))
        if active_status is not None
        else run_row.status == "running"
    )
    status = "running" if running else run_row.status
    health = strategy_health(
        datetime.fromisoformat(heartbeat) if heartbeat else None,
        status=status,
        error=run_row.error_message,
    )
    return {
        "strategy_id": strategy_id,
        "name": run_row.strategy_name,
        "version": run_row.strategy_version,
        "mode": run_row.mode,
        "status": status,
        "health": health.health,
        "heartbeat_age_seconds": health.heartbeat_age_seconds,
        "last_heartbeat": heartbeat,
        "started_at": _runtime_timestamp(run_row.started_at),
        "stopped_at": _runtime_timestamp(run_row.stopped_at),
        "parameters": active_status.get("parameters", {})
        if active_status is not None
        else json.loads(run_row.parameters_json or "{}"),
        "state": active_status.get("state", {})
        if active_status is not None
        else json.loads(run_row.state_json or "{}"),
        "order_count": total_order_count,
        "pending_order_count": pending_order_count,
        "trade_count": total_trade_count,
        "risk_event_count": total_risk_count,
        "record_count": _monitor_int(active_status.get("record_count", 0))
        if active_status is not None
        else 0,
        "runtime_seconds": strategy_runtime_seconds(
            run_row.started_at,
            stopped_at=run_row.stopped_at if not running else None,
        ),
        "last_activity_at": heartbeat,
        "process_role": "paper-strategy-runner" if run_row.mode == "paper" else "strategy-runtime",
        "recovery_status": strategy_recovery_status(
            status=status,
            attached=active is not None,
        ),
        "live_order_policy": "blocked",
        "can_send_live_orders": False,
        "orders": [
            {
                "order_id": row.order_id,
                "client_order_id": row.client_order_id,
                "symbol": row.symbol,
                "direction": row.direction,
                "quantity": row.quantity,
                "filled_quantity": row.filled_quantity,
                "status": row.status,
                "execution_mode": row.execution_mode,
                "qmt_order_id": row.qmt_order_id,
                "last_error": row.last_error,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in orders
        ],
        "trades": [
            {
                "trade_id": trade.trade_id,
                "order_id": trade.order_id,
                "symbol": trade.symbol,
                "direction": order.direction,
                "quantity": trade.quantity,
                "price": str(trade.price),
                "fees": str(trade.fees),
                "broker_reported_fee": (
                    str(trade.broker_reported_fee)
                    if trade.broker_reported_fee is not None
                    else None
                ),
                "fee_source": trade.fee_source,
                "fee_breakdown_complete": trade.fee_breakdown_complete,
                "source": trade.source,
                "data_origin": trade.data_origin,
                "is_simulated": trade.is_simulated,
                "timestamp": trade.timestamp.isoformat(),
            }
            for trade, order in trades
        ],
        "risk_events": [
            {
                "level": row.level,
                "code": row.code,
                "message": row.message,
                "order_id": row.order_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in risks
        ],
        "audit_events": [
            {
                "event_type": row.event_type,
                "actor": row.actor,
                "created_at": row.created_at.isoformat(),
            }
            for row in audits
        ],
    }


@router.get("/monitoring/account")
async def account_monitor() -> dict[str, object]:
    snapshot = account_monitor_snapshot(trading_engine_client.status())
    async with SessionFactory() as session:
        risk_rows = (
            await session.scalars(
                select(RiskEventRecord).order_by(desc(RiskEventRecord.created_at)).limit(50)
            )
        ).all()
    snapshot["recent_risk_events"] = [
        {
            "level": row.level,
            "code": row.code,
            "message": row.message,
            "strategy_id": row.strategy_id,
            "order_id": row.order_id,
            "created_at": row.created_at.isoformat(),
        }
        for row in risk_rows
    ]
    return snapshot


@router.get("/monitoring/delivery-statements")
async def delivery_statements(
    start_date: str = Query(...),
    end_date: str = Query(...),
    symbol: str | None = Query(default=None),
    strategy_id: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, object]:
    return await _delivery_statement_data(
        start_date=start_date,
        end_date=end_date,
        symbol=symbol,
        strategy_id=strategy_id,
        limit=limit,
    )


@router.get("/monitoring/delivery-statements/export.csv")
async def export_delivery_statements(
    start_date: str = Query(...),
    end_date: str = Query(...),
    symbol: str | None = Query(default=None),
    strategy_id: str | None = Query(default=None),
) -> StreamingResponse:
    payload = await _delivery_statement_data(
        start_date=start_date,
        end_date=end_date,
        symbol=symbol,
        strategy_id=strategy_id,
        limit=10_000,
    )
    records = payload["records"]
    assert isinstance(records, list)
    output = io.StringIO()
    fields = [
        "trade_date",
        "timestamp",
        "symbol",
        "business_type",
        "direction",
        "quantity",
        "price",
        "notional",
        "fees",
        "settlement_amount",
        "strategy_id",
        "execution_mode",
        "account",
        "trade_id",
        "order_id",
        "client_order_id",
        "order_sysid",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    content = "\ufeff" + output.getvalue()
    filename = f"delivery-statements-{start_date}-{end_date}.csv"
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.websocket("/ws/monitor")
async def monitor_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            settings = get_settings()
            market = get_xtquant_status()
            paper = paper_service.snapshot()
            execution = trading_engine_client.status()
            await websocket.send_json(
                {
                    "type": "monitor",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "live_trading_enabled": settings.enable_live_trading,
                    "miniqmt_connected": market.connected,
                    "paper_account": paper["account"],
                    "paper_risk": paper["risk"],
                    "paper_reconciled_at": paper["last_reconciled_at"],
                    "execution": execution,
                    "account_monitor": account_monitor_snapshot(execution),
                    "strategies": [item.status() for item in paper_runners.values()],
                    "realtime": realtime_service.snapshot(),
                    "xtdata_subscription": xtdata_subscription_service.snapshot(),
                }
            )
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=2)
                if message["type"] == "websocket.disconnect":
                    return
            except TimeoutError:
                continue
    except (WebSocketDisconnect, RuntimeError, OSError):
        return
