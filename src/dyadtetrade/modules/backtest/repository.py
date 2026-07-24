from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.models import (
    BacktestDailyEquityRecord,
    BacktestOrderRecord,
    BacktestResultRecord,
    BacktestTaskRecord,
    BacktestTradeRecord,
)
from dyadtetrade.domain.precision import api_decimal
from dyadtetrade.modules.backtest.result import BacktestResult
from dyadtetrade.modules.reporting import ReportArtifacts


async def find_task_by_key(
    session: AsyncSession, idempotency_key: str
) -> BacktestTaskRecord | None:
    return cast(
        BacktestTaskRecord | None,
        await session.scalar(
            select(BacktestTaskRecord).where(BacktestTaskRecord.idempotency_key == idempotency_key)
        ),
    )


async def get_task(session: AsyncSession, task_id: int) -> BacktestTaskRecord | None:
    return await session.get(BacktestTaskRecord, task_id)


async def list_tasks(session: AsyncSession, limit: int = 50) -> list[BacktestTaskRecord]:
    return list(
        (
            await session.scalars(
                select(BacktestTaskRecord).order_by(BacktestTaskRecord.id.desc()).limit(limit)
            )
        ).all()
    )


async def persist_result(
    session: AsyncSession,
    task: BacktestTaskRecord,
    result: BacktestResult,
    artifacts: ReportArtifacts,
) -> BacktestResultRecord:
    record = BacktestResultRecord(
        run_id=result.run_id,
        task_id=task.id,
        metrics_json=json.dumps(result.metrics, sort_keys=True),
        report_json=str(artifacts.json_path),
        report_html=str(artifacts.html_path),
        report_pdf=str(artifacts.pdf_path),
        report_svg=str(artifacts.svg_path),
    )
    session.add(record)
    await session.flush()
    session.add_all(
        [
            BacktestOrderRecord(
                result_id=record.id,
                order_id=order.order_id,
                client_order_id=order.request.client_order_id,
                symbol=order.request.symbol,
                direction=order.request.direction.value,
                quantity=order.request.quantity,
                filled_quantity=order.filled_quantity,
                status=order.status.value,
                average_fill_price=order.average_fill_price,
                submitted_at=order.submitted_at,
            )
            for order in result.orders
        ]
    )
    session.add_all(
        [
            BacktestTradeRecord(
                result_id=record.id,
                trade_id=trade.trade_id,
                order_id=trade.order_id,
                symbol=trade.symbol,
                direction=trade.direction.value,
                quantity=trade.quantity,
                price=trade.price,
                commission=trade.commission,
                tax=trade.tax,
                transfer_fee=trade.transfer_fee,
                timestamp=trade.timestamp,
            )
            for trade in result.trades
        ]
    )
    session.add_all(
        [
            BacktestDailyEquityRecord(
                result_id=record.id,
                trade_date=item.date,
                cash=item.cash,
                positions_value=item.positions_value,
                total_assets=item.total_assets,
                return_value=float(item.return_value),
                drawdown=float(item.drawdown),
                net_value=float(item.net_value),
                benchmark_net_value=(
                    float(item.benchmark_net_value)
                    if item.benchmark_net_value is not None
                    else None
                ),
                relative_net_value=(
                    float(item.relative_net_value) if item.relative_net_value is not None else None
                ),
            )
            for item in result.daily_equity
        ]
    )
    task.result_id = record.id
    await session.commit()
    return record


async def result_detail(session: AsyncSession, result_id: int) -> dict[str, object] | None:
    result = await session.get(BacktestResultRecord, result_id)
    if result is None:
        return None
    orders = list(
        (
            await session.scalars(
                select(BacktestOrderRecord)
                .where(BacktestOrderRecord.result_id == result_id)
                .order_by(BacktestOrderRecord.id)
            )
        ).all()
    )
    trades = list(
        (
            await session.scalars(
                select(BacktestTradeRecord)
                .where(BacktestTradeRecord.result_id == result_id)
                .order_by(BacktestTradeRecord.timestamp)
            )
        ).all()
    )
    equity = list(
        (
            await session.scalars(
                select(BacktestDailyEquityRecord)
                .where(BacktestDailyEquityRecord.result_id == result_id)
                .order_by(BacktestDailyEquityRecord.trade_date)
            )
        ).all()
    )
    report_prefix = Path(result.report_json).with_suffix("") if result.report_json else None

    def csv_artifact(suffix: str) -> str | None:
        if report_prefix is None:
            return None
        candidate = report_prefix.parent / f"{report_prefix.name}_{suffix}.csv"
        return str(candidate) if candidate.is_file() else None

    return {
        "id": result.id,
        "run_id": result.run_id,
        "metrics": json.loads(result.metrics_json),
        "reports": {
            "json": result.report_json,
            "html": result.report_html,
            "pdf": result.report_pdf,
            "svg": result.report_svg,
            "equity_csv": csv_artifact("equity"),
            "orders_csv": csv_artifact("orders"),
            "trades_csv": csv_artifact("trades"),
            "positions_csv": csv_artifact("positions"),
            "fees_csv": csv_artifact("fees"),
        },
        "orders": [
            {
                "order_id": item.order_id,
                "symbol": item.symbol,
                "direction": item.direction,
                "quantity": item.quantity,
                "filled_quantity": item.filled_quantity,
                "status": item.status,
                "average_fill_price": api_decimal(item.average_fill_price),
            }
            for item in orders
        ],
        "trades": [
            {
                "trade_id": item.trade_id,
                "order_id": item.order_id,
                "symbol": item.symbol,
                "direction": item.direction,
                "quantity": item.quantity,
                "price": api_decimal(item.price),
                "commission": api_decimal(item.commission),
                "tax": api_decimal(item.tax),
                "transfer_fee": api_decimal(item.transfer_fee),
                "timestamp": item.timestamp.isoformat(),
            }
            for item in trades
        ],
        "equity": [
            {
                "date": item.trade_date,
                "cash": api_decimal(item.cash),
                "positions_value": api_decimal(item.positions_value),
                "total_assets": api_decimal(item.total_assets),
                "return_value": item.return_value,
                "drawdown": item.drawdown,
                "net_value": item.net_value,
                "benchmark_net_value": item.benchmark_net_value,
                "relative_net_value": item.relative_net_value,
            }
            for item in equity
        ],
    }
