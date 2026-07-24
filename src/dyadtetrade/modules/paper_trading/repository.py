from __future__ import annotations

import json
from datetime import UTC
from decimal import Decimal

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.models import (
    OmsOrderRecord,
    OmsTradeRecord,
    PaperAccountRecord,
    PositionRecord,
    RiskEventRecord,
    StrategyRunRecord,
)
from dyadtetrade.domain import (
    Direction,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    StrategyState,
    Trade,
)
from dyadtetrade.modules.paper_trading.runner import PaperStrategyRunner
from dyadtetrade.modules.paper_trading.service import PaperTradingService


async def save_paper_state(session: AsyncSession, service: PaperTradingService) -> None:
    account = service.broker.account
    account_record = await session.scalar(
        select(PaperAccountRecord).where(PaperAccountRecord.account_id == account.account_id)
    )
    if account_record is None:
        account_record = PaperAccountRecord(
            account_id=account.account_id,
            cash=account.cash,
            available_cash=account.available_cash,
            frozen_cash=account.frozen_cash,
        )
        session.add(account_record)
    else:
        account_record.cash = account.cash
        account_record.available_cash = account.available_cash
        account_record.frozen_cash = account.frozen_cash
    account_record.last_reconciled_at = service.last_reconciled_at

    await session.execute(
        delete(PositionRecord).where(PositionRecord.account_id == account.account_id)
    )
    session.add_all(
        [
            PositionRecord(
                account_id=account.account_id,
                symbol=position.symbol,
                quantity=position.quantity,
                available_quantity=position.available_quantity,
                frozen_quantity=position.frozen_quantity,
                average_cost=position.average_cost,
                last_price=position.last_price,
            )
            for position in account.positions.values()
        ]
    )
    for order in service.oms.orders.values():
        existing = await session.scalar(
            select(OmsOrderRecord).where(
                or_(
                    OmsOrderRecord.order_id == order.order_id,
                    OmsOrderRecord.client_order_id == order.request.client_order_id,
                    OmsOrderRecord.idempotency_key == order.request.idempotency_key,
                )
            )
        )
        payload = json.dumps(
            {
                "limit_price": str(order.request.limit_price)
                if order.request.limit_price is not None
                else None,
                "average_fill_price": str(order.average_fill_price),
                "reject_reason": order.reject_reason,
            }
        )
        if existing is None:
            session.add(
                OmsOrderRecord(
                    order_id=order.order_id,
                    client_order_id=order.request.client_order_id,
                    idempotency_key=order.request.idempotency_key,
                    account_id=order.request.account_id,
                    strategy_id=order.request.strategy_id,
                    parent_order_id=order.request.parent_order_id,
                    symbol=order.request.symbol,
                    direction=order.request.direction.value,
                    quantity=order.request.quantity,
                    filled_quantity=order.filled_quantity,
                    limit_price=order.request.limit_price,
                    average_fill_price=order.average_fill_price,
                    status=order.status.value,
                    payload_json=payload,
                )
            )
        else:
            # A recovered/replayed client request can produce a fresh transient
            # domain id before OMS idempotency collapses it.  Keep the durable
            # identity and update only the latest state instead of violating a
            # unique client/idempotency constraint.
            existing.filled_quantity = order.filled_quantity
            existing.status = order.status.value
            existing.limit_price = order.request.limit_price
            existing.average_fill_price = order.average_fill_price
            existing.payload_json = payload
    for trade in service.oms.trades.values():
        existing_trade = await session.scalar(
            select(OmsTradeRecord.id).where(OmsTradeRecord.trade_id == trade.trade_id)
        )
        if existing_trade is None:
            session.add(
                OmsTradeRecord(
                    trade_id=trade.trade_id,
                    order_id=trade.order_id,
                    account_id=account.account_id,
                    symbol=trade.symbol,
                    quantity=trade.quantity,
                    direction=trade.direction.value,
                    price=trade.price,
                    fees=trade.total_fees,
                    commission=trade.commission,
                    tax=trade.tax,
                    transfer_fee=trade.transfer_fee,
                    execution_mode="paper",
                    strategy_id=(
                        service.oms.orders[trade.order_id].request.strategy_id
                        if trade.order_id in service.oms.orders
                        else None
                    ),
                    source="paper_match",
                    is_simulated=True,
                    timestamp=trade.timestamp,
                )
            )
    for event in service.risk.events:
        checked_at = event.checked_at.replace(tzinfo=None)
        exists = await session.scalar(
            select(RiskEventRecord.id).where(
                RiskEventRecord.account_id == account.account_id,
                RiskEventRecord.code == event.code,
                RiskEventRecord.created_at == checked_at,
            )
        )
        if exists is None:
            session.add(
                RiskEventRecord(
                    account_id=account.account_id,
                    strategy_id=str(event.details.get("strategy_id", "")) or None,
                    order_id=str(event.details.get("order_id", "")) or None,
                    level="warning",
                    code=event.code,
                    message=event.message,
                    details_json=json.dumps(event.details, ensure_ascii=False),
                    created_at=checked_at,
                )
            )
    await session.commit()


async def recover_paper_state(session: AsyncSession, service: PaperTradingService) -> bool:
    account_record = await session.scalar(
        select(PaperAccountRecord).where(PaperAccountRecord.account_id == "paper")
    )
    if account_record is None:
        return False
    account = service.broker.account
    account.account_id = account_record.account_id
    account.cash = account_record.cash
    account.available_cash = account_record.available_cash
    account.frozen_cash = account_record.frozen_cash
    account.positions.clear()
    positions = (
        await session.scalars(
            select(PositionRecord).where(PositionRecord.account_id == account_record.account_id)
        )
    ).all()
    for position_row in positions:
        account.positions[position_row.symbol] = Position(
            symbol=position_row.symbol,
            quantity=position_row.quantity,
            available_quantity=position_row.available_quantity,
            frozen_quantity=position_row.frozen_quantity,
            average_cost=position_row.average_cost,
            last_price=position_row.last_price,
        )
    order_rows = (
        await session.scalars(
            select(OmsOrderRecord).where(OmsOrderRecord.account_id == account_record.account_id)
        )
    ).all()
    for order_row in order_rows:
        details = json.loads(order_row.payload_json)
        request = OrderRequest(
            symbol=order_row.symbol,
            direction=Direction(order_row.direction),
            quantity=order_row.quantity,
            strategy_id=order_row.strategy_id,
            account_id=order_row.account_id,
            client_order_id=order_row.client_order_id,
            idempotency_key=order_row.idempotency_key,
            parent_order_id=order_row.parent_order_id,
            limit_price=(
                order_row.limit_price
                if order_row.limit_price is not None
                else Decimal(details["limit_price"])
                if details.get("limit_price") is not None
                else None
            ),
        )
        order = Order(
            order_id=order_row.order_id,
            request=request,
            status=OrderStatus(order_row.status),
            filled_quantity=order_row.filled_quantity,
            average_fill_price=order_row.average_fill_price,
            reject_reason=details.get("reject_reason"),
        )
        service.oms.register(order, order_row.updated_at)
        service.broker.restore_order(order)
    trade_rows = (
        await session.scalars(
            select(OmsTradeRecord).where(OmsTradeRecord.account_id == account_record.account_id)
        )
    ).all()
    for trade_row in trade_rows:
        related = service.oms.orders.get(trade_row.order_id)
        direction = related.request.direction if related else Direction.BUY
        service.oms.record_trade(
            Trade(
                trade_id=trade_row.trade_id,
                order_id=trade_row.order_id,
                symbol=trade_row.symbol,
                direction=direction,
                quantity=trade_row.quantity,
                price=trade_row.price,
                timestamp=trade_row.timestamp,
                commission=trade_row.commission,
                tax=trade_row.tax,
                transfer_fee=trade_row.transfer_fee,
                execution_mode="paper",
                account_id=trade_row.account_id,
                strategy_id=trade_row.strategy_id or "",
                run_id=trade_row.run_id,
                source=trade_row.source,
                is_simulated=True,
            )
        )
    service.last_reconciled_at = account_record.last_reconciled_at
    service.broker.account.assert_identity()
    return True


async def save_paper_runners(
    session: AsyncSession, runners: dict[str, PaperStrategyRunner]
) -> None:
    for strategy_id, runner in runners.items():
        row = await session.scalar(
            select(StrategyRunRecord).where(StrategyRunRecord.strategy_instance_id == strategy_id)
        )
        state = runner.snapshot()
        if row is None:
            row = StrategyRunRecord(
                strategy_instance_id=strategy_id,
                strategy_name=runner.strategy.name,
                strategy_version=runner.strategy.version,
                mode="paper",
            )
            session.add(row)
        row.status = "running" if runner.running else "stopped"
        row.strategy_version = runner.strategy.version
        row.parameters_json = json.dumps(runner.parameters, ensure_ascii=False, default=str)
        row.state_json = json.dumps(state.payload, ensure_ascii=False, default=str)
        row.random_seed = state.random_seed
        row.last_heartbeat = (
            runner.last_heartbeat.replace(tzinfo=None) if runner.last_heartbeat else None
        )
        row.started_at = runner.started_at.replace(tzinfo=None) if runner.started_at else None
        row.stopped_at = runner.stopped_at.replace(tzinfo=None) if runner.stopped_at else None
        row.error_message = None
    await session.commit()


async def recover_paper_runners(
    session: AsyncSession, service: PaperTradingService
) -> dict[str, PaperStrategyRunner]:
    rows = (
        await session.scalars(
            select(StrategyRunRecord)
            .where(
                StrategyRunRecord.mode == "paper",
                StrategyRunRecord.status == "running",
            )
            .order_by(StrategyRunRecord.updated_at.desc())
        )
    ).all()
    recovered: dict[str, PaperStrategyRunner] = {}
    recovered_singletons: set[str] = set()
    for row in rows:
        if (
            row.strategy_name == "tick_momentum_small_cap"
            and row.strategy_name in recovered_singletons
        ):
            row.status = "stopped"
            row.error_message = "duplicate singleton strategy stopped during recovery"
            continue
        try:
            parameters = dict(json.loads(row.parameters_json))
            parameters["random_seed"] = row.random_seed
            if row.strategy_name == "tick_momentum_small_cap":
                parameters["rebalance_every_ticks"] = max(
                    int(parameters.get("rebalance_every_ticks", 3000)), 3000
                )
                parameters.setdefault("max_total_weight", "0.25")
                parameters.setdefault("max_single_weight", "0.05")
            runner = PaperStrategyRunner(
                service,
                row.strategy_name,
                row.strategy_instance_id,
                parameters,
            )
            runner.start()
            state_version = row.strategy_version
            if row.strategy_name == "tick_momentum_small_cap" and row.strategy_version in {
                "1.0.0",
                "1.1.0",
            }:
                state_version = runner.strategy.version
            runner.restore(
                StrategyState(
                    row.strategy_instance_id,
                    state_version,
                    row.updated_at.replace(tzinfo=UTC),
                    dict(json.loads(row.state_json)),
                    row.random_seed,
                )
            )
            if row.started_at is not None and row.started_at.year >= 2000:
                runner.started_at = row.started_at.replace(tzinfo=UTC)
            runner.last_heartbeat = (
                row.last_heartbeat.replace(tzinfo=UTC) if row.last_heartbeat else None
            )
            if runner.last_heartbeat is not None:
                service.heartbeat_strategy(row.strategy_instance_id, runner.last_heartbeat)
            recovered[row.strategy_instance_id] = runner
            if row.strategy_name == "tick_momentum_small_cap":
                recovered_singletons.add(row.strategy_name)
        except Exception as exc:
            row.status = "failed"
            row.error_message = f"{type(exc).__name__}: {exc}"
    await session.commit()
    return recovered
