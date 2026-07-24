from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dyadtetrade.data.backtest.models import BacktestRunResult, HistoricalPosition
from dyadtetrade.data.sqlite.connection import connect_sqlite, write_transaction

BACKTEST_DDL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    period TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    bars_processed INTEGER NOT NULL,
    final_cash TEXT NOT NULL,
    final_assets TEXT NOT NULL,
    config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_orders (
    order_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    submitted_ts INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    filled_ts INTEGER,
    filled_price TEXT
);
CREATE TABLE IF NOT EXISTS backtest_trades (
    trade_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price TEXT NOT NULL,
    commission TEXT NOT NULL,
    stamp_duty TEXT NOT NULL,
    transfer_fee TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_equity (
    ts INTEGER PRIMARY KEY,
    cash TEXT NOT NULL,
    position_value TEXT NOT NULL,
    total_assets TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL,
    available INTEGER NOT NULL,
    average_cost TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_logs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


class BacktestResultRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save(
        self,
        result: BacktestRunResult,
        *,
        strategy_name: str,
        period: str,
        config: dict[str, object],
        positions: dict[str, HistoricalPosition],
        logs: list[tuple[int | None, str, str]],
    ) -> None:
        with connect_sqlite(self.path, wal=False) as connection:
            connection.executescript(BACKTEST_DDL)
            with write_transaction(connection):
                connection.execute(
                    "INSERT INTO backtest_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.run_id,
                        strategy_name,
                        period,
                        result.start_ts,
                        result.end_ts,
                        result.bars_processed,
                        str(result.final_cash),
                        str(result.final_assets),
                        json.dumps(config, ensure_ascii=False, sort_keys=True, default=str),
                    ),
                )
                connection.executemany(
                    "INSERT INTO backtest_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            order.order_id,
                            order.symbol,
                            order.side,
                            order.quantity,
                            order.submitted_ts,
                            order.status,
                            order.reason,
                            order.filled_ts,
                            None if order.filled_price is None else str(order.filled_price),
                        )
                        for order in result.orders
                    ],
                )
                connection.executemany(
                    "INSERT INTO backtest_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            trade.trade_id,
                            trade.order_id,
                            trade.symbol,
                            trade.side,
                            trade.quantity,
                            str(trade.price),
                            str(trade.commission),
                            str(trade.stamp_duty),
                            str(trade.transfer_fee),
                            trade.ts,
                        )
                        for trade in result.trades
                    ],
                )
                connection.executemany(
                    "INSERT INTO backtest_equity VALUES (?, ?, ?, ?)",
                    [
                        (
                            point.ts,
                            str(point.cash),
                            str(point.position_value),
                            str(point.total_assets),
                        )
                        for point in result.equity
                    ],
                )
                connection.executemany(
                    "INSERT INTO backtest_positions VALUES (?, ?, ?, ?)",
                    [
                        (
                            position.symbol,
                            position.quantity,
                            position.available,
                            str(position.average_cost),
                        )
                        for position in positions.values()
                    ],
                )
                connection.executemany(
                    "INSERT INTO backtest_logs(ts, level, message) VALUES (?, ?, ?)", logs
                )

    def integrity(self) -> str:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return "missing" if row is None else str(row[0])
