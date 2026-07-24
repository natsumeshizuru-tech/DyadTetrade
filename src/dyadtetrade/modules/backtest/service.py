from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.duckdb_store import query_bars
from dyadtetrade.db.models import BacktestTaskRecord, DataFile
from dyadtetrade.modules.backtest.engine import BacktestEngine
from dyadtetrade.modules.backtest.repository import (
    find_task_by_key,
    get_task,
    persist_result,
)
from dyadtetrade.modules.backtest.schemas import BacktestConfig
from dyadtetrade.modules.performance import analyze_performance
from dyadtetrade.modules.reporting import generate_backtest_report

BACKTEST_CONTRACT_VERSION = "2026.07.12.3"


class BacktestService:
    def __init__(self, *, report_root: Path = Path("reports/backtests")) -> None:
        self.report_root = report_root

    async def create_task(
        self,
        session: AsyncSession,
        config: BacktestConfig,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[BacktestTaskRecord, bool]:
        if idempotency_key is None:
            data_rows = (
                await session.execute(
                    select(DataFile.symbol, DataFile.checksum, DataFile.updated_at)
                    .where(
                        DataFile.symbol.in_(config.symbols),
                        DataFile.layer == "bt_ready",
                        DataFile.period == "1d",
                    )
                    .order_by(DataFile.symbol)
                )
            ).all()
            data_signature = [
                (str(symbol), str(checksum), updated_at.isoformat())
                for symbol, checksum, updated_at in data_rows
            ]
            key_payload = json.dumps(
                {
                    "contract": BACKTEST_CONTRACT_VERSION,
                    "config": json.loads(config.model_dump_json()),
                    "data": data_signature,
                },
                sort_keys=True,
            )
            key = hashlib.sha256(key_payload.encode()).hexdigest()
        else:
            key = idempotency_key
        existing = await find_task_by_key(session, key)
        if existing is not None:
            return existing, False
        task = BacktestTaskRecord(
            idempotency_key=key,
            status="pending",
            progress=0.0,
            strategy_name=config.strategy_name,
            config_json=config.model_dump_json(),
            symbols_json=json.dumps(config.symbols),
            data_version=config.data_version,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task, True

    async def run_task(
        self,
        session: AsyncSession,
        task_id: int,
        *,
        frame: pd.DataFrame | None = None,
    ) -> BacktestTaskRecord:
        task = await get_task(session, task_id)
        if task is None:
            raise LookupError(f"backtest task not found: {task_id}")
        if task.status == "succeeded" and task.result_id is not None:
            return task
        if task.cancel_requested:
            task.status = "cancelled"
            await session.commit()
            return task
        config = BacktestConfig.model_validate_json(task.config_json)
        task.status = "running"
        task.progress = 5.0
        task.started_at = datetime.now()
        await session.commit()
        try:
            input_frame = frame if frame is not None else self._load_frame(config)
            task.progress = 30.0
            await session.commit()
            result = BacktestEngine().run(config, input_frame)
            task.data_fingerprint = result.data_fingerprint
            task.strategy_version = result.strategy_version
            performance = analyze_performance(
                result.equity_frame(),
                turnover_notional=sum(float(trade.notional) for trade in result.trades),
                trade_count=len(result.trades),
                position_values=pd.Series(result.final_positions, dtype=float),
                benchmark=BacktestEngine.benchmark_series(config, input_frame),
            )
            result.metrics = {
                key: float(value)
                for key, value in performance.metrics.items()
                if isinstance(value, int | float)
            }
            artifacts = generate_backtest_report(
                result,
                performance,
                self.report_root / f"task_{task.id}",
                market_bars=input_frame,
                primary_symbol=config.symbols[0],
            )
            task.progress = 85.0
            await persist_result(session, task, result, artifacts)
            task.status = "succeeded"
            task.progress = 100.0
            task.finished_at = datetime.now()
            await session.commit()
        except Exception as exc:
            await session.rollback()
            task = await get_task(session, task_id)
            assert task is not None
            task.status = "failed"
            task.error_message = f"{type(exc).__name__}: {exc}"
            task.finished_at = datetime.now()
            await session.commit()
            raise
        return task

    async def cancel_task(self, session: AsyncSession, task_id: int) -> BacktestTaskRecord:
        task = await get_task(session, task_id)
        if task is None:
            raise LookupError(task_id)
        task.cancel_requested = True
        if task.status == "pending":
            task.status = "cancelled"
        await session.commit()
        return task

    @staticmethod
    def _load_frame(config: BacktestConfig) -> pd.DataFrame:
        start = config.start_date.strftime("%Y%m%d") if config.start_date else "19000101"
        end = config.end_date.strftime("%Y%m%d") if config.end_date else "29991231"
        symbols = list(config.symbols)
        if config.benchmark_symbol and config.benchmark_symbol not in symbols:
            symbols.append(config.benchmark_symbol)
        records = query_bars(
            symbols=symbols,
            start_date=start,
            end_date=end,
            layer="bt_ready",
            limit=2_000_000,
        )
        if not records:
            raise ValueError("no bt_ready data found for requested backtest")
        return pd.DataFrame(records)


def task_to_dict(task: BacktestTaskRecord) -> dict[str, object]:
    return {
        "id": task.id,
        "status": task.status,
        "progress": task.progress,
        "strategy_name": task.strategy_name,
        "strategy_version": task.strategy_version,
        "symbols": json.loads(task.symbols_json),
        "result_id": task.result_id,
        "error_message": task.error_message,
        "data_fingerprint": task.data_fingerprint,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }
