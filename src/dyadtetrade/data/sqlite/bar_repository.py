from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from dyadtetrade.data.config import StorageConfig
from dyadtetrade.data.models import BarRecord
from dyadtetrade.data.sqlite.connection import connect_sqlite, integrity_check, write_transaction
from dyadtetrade.data.sqlite.migrations import migrate_bars
from dyadtetrade.data.sqlite.shard_manager import ShardManager

BAR_COLUMNS = (
    "symbol, ts, trade_date, open, high, low, close, volume, amount, "
    "settlement_price, open_interest, pre_close, suspend_flag, source, ingested_at, source_hash"
)


@dataclass(frozen=True, slots=True)
class RowWriteCounts:
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int


@dataclass(frozen=True, slots=True)
class UpsertResult:
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int
    shards: tuple[Path, ...]
    by_symbol: dict[str, RowWriteCounts]


class BarRepository:
    def __init__(self, storage: StorageConfig) -> None:
        self.storage = storage
        self.shards = ShardManager(storage)

    def initialize(self) -> None:
        for path in (self.shards.daily_path,):
            with connect_sqlite(
                path, wal=self.storage.wal, busy_timeout_ms=self.storage.busy_timeout_ms
            ) as connection:
                migrate_bars(connection)

    def upsert(self, period: str, bars: list[BarRecord]) -> UpsertResult:
        inserted = 0
        updated = 0
        unchanged = 0
        paths: list[Path] = []
        by_symbol: dict[str, list[int]] = {}
        for path, shard_bars in self.shards.group_bars(period, bars).items():
            paths.append(path)
            with connect_sqlite(
                path, wal=self.storage.wal, busy_timeout_ms=self.storage.busy_timeout_ms
            ) as connection:
                migrate_bars(connection)
                with write_transaction(connection):
                    existing = self._existing_hashes(connection, shard_bars)
                    connection.executemany(
                        f"""
                        INSERT INTO bars ({BAR_COLUMNS})
                        VALUES ({",".join("?" for _ in range(16))})
                        ON CONFLICT(symbol, ts) DO UPDATE SET
                            trade_date=excluded.trade_date,
                            open=excluded.open,
                            high=excluded.high,
                            low=excluded.low,
                            close=excluded.close,
                            volume=excluded.volume,
                            amount=excluded.amount,
                            settlement_price=excluded.settlement_price,
                            open_interest=excluded.open_interest,
                            pre_close=excluded.pre_close,
                            suspend_flag=excluded.suspend_flag,
                            source=excluded.source,
                            ingested_at=excluded.ingested_at,
                            source_hash=excluded.source_hash
                        WHERE bars.source_hash IS NOT excluded.source_hash
                        """,
                        [bar.sqlite_values() for bar in shard_bars],
                    )
                for bar in shard_bars:
                    counts = by_symbol.setdefault(bar.symbol, [0, 0, 0])
                    old_hash = existing.get((bar.symbol, bar.ts))
                    if (bar.symbol, bar.ts) not in existing:
                        inserted += 1
                        counts[0] += 1
                    elif old_hash != bar.source_hash:
                        updated += 1
                        counts[1] += 1
                    else:
                        unchanged += 1
                        counts[2] += 1
        return UpsertResult(
            inserted,
            updated,
            unchanged,
            tuple(sorted(paths)),
            {symbol: RowWriteCounts(*counts) for symbol, counts in by_symbol.items()},
        )

    @staticmethod
    def _existing_hashes(
        connection: sqlite3.Connection, bars: list[BarRecord]
    ) -> dict[tuple[str, int], str | None]:
        result: dict[tuple[str, int], str | None] = {}
        if not bars:
            return result
        symbols = sorted({bar.symbol for bar in bars})
        first_ts = min(bar.ts for bar in bars)
        last_ts = max(bar.ts for bar in bars)
        for offset in range(0, len(symbols), 900):
            batch = symbols[offset : offset + 900]
            placeholders = ",".join("?" for _ in batch)
            for row in connection.execute(
                "SELECT symbol, ts, source_hash FROM bars "
                f"WHERE symbol IN ({placeholders}) AND ts BETWEEN ? AND ?",
                [*batch, first_ts, last_ts],
            ):
                result[(str(row[0]), int(row[1]))] = None if row[2] is None else str(row[2])
        return result

    def query(
        self,
        symbols: list[str],
        *,
        period: str,
        start_date: int,
        end_date: int,
    ) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame(columns=[item.strip() for item in BAR_COLUMNS.split(",")])
        if period == "1d":
            paths = [self.shards.daily_path]
        elif period == "1m":
            paths = self.shards.minute_paths_between(symbols, start_date, end_date)
        else:
            raise ValueError(f"query supports 1d and 1m raw storage, got {period}")
        frames: list[pd.DataFrame] = []
        placeholders = ",".join("?" for _ in symbols)
        for path in paths:
            if not path.exists():
                continue
            with connect_sqlite(
                path, readonly=True, busy_timeout_ms=self.storage.busy_timeout_ms
            ) as connection:
                rows = connection.execute(
                    f"SELECT {BAR_COLUMNS} FROM bars "
                    f"WHERE symbol IN ({placeholders}) AND trade_date BETWEEN ? AND ? "
                    "ORDER BY ts, symbol",
                    [*symbols, start_date, end_date],
                ).fetchall()
                if rows:
                    frames.append(pd.DataFrame([dict(row) for row in rows]))
        if not frames:
            return pd.DataFrame(columns=[item.strip() for item in BAR_COLUMNS.split(",")])
        result = pd.concat(frames, ignore_index=True)
        return result.sort_values(["ts", "symbol"], kind="stable").reset_index(drop=True)

    def integrity(self, period: str, symbols: list[str], start: int, end: int) -> dict[str, str]:
        paths = (
            [self.shards.daily_path]
            if period == "1d"
            else self.shards.minute_paths_between(symbols, start, end)
        )
        result: dict[str, str] = {}
        for path in paths:
            if path.exists():
                with connect_sqlite(path, readonly=True) as connection:
                    result[str(path)] = integrity_check(connection)
        return result

    def symbol_stats(self, period: str, symbol: str) -> tuple[int | None, int | None, int]:
        if period == "1d":
            paths = [self.shards.daily_path]
        elif period == "1m":
            market = symbol.rsplit(".", 1)[-1]
            paths = sorted(self.storage.root.glob(f"bars_1m_{market}_*.db"))
        else:
            raise ValueError(f"unsupported bar period: {period}")
        first: int | None = None
        last: int | None = None
        count = 0
        for path in paths:
            if not path.exists():
                continue
            with connect_sqlite(path, readonly=True) as connection:
                row = connection.execute(
                    "SELECT min(ts), max(ts), count(*) FROM bars WHERE symbol=?", (symbol,)
                ).fetchone()
                if row is None or int(row[2]) == 0:
                    continue
                shard_first = int(row[0])
                shard_last = int(row[1])
                first = shard_first if first is None else min(first, shard_first)
                last = shard_last if last is None else max(last, shard_last)
                count += int(row[2])
        return first, last, count

    def symbol_stats_many(
        self, period: str, symbols: list[str]
    ) -> dict[str, tuple[int | None, int | None, int]]:
        if not symbols:
            return {}
        if period == "1d":
            paths = [self.shards.daily_path]
        elif period == "1m":
            markets = {symbol.rsplit(".", 1)[-1] for symbol in symbols}
            paths = sorted(
                path
                for market in markets
                for path in self.storage.root.glob(f"bars_1m_{market}_*.db")
            )
        else:
            raise ValueError(f"unsupported bar period: {period}")
        result: dict[str, tuple[int | None, int | None, int]] = {
            symbol: (None, None, 0) for symbol in symbols
        }
        placeholders = ",".join("?" for _ in symbols)
        for path in paths:
            if not path.exists():
                continue
            with connect_sqlite(path, readonly=True) as connection:
                rows = connection.execute(
                    "SELECT symbol, min(ts), max(ts), count(*) FROM bars "
                    f"WHERE symbol IN ({placeholders}) GROUP BY symbol",
                    symbols,
                ).fetchall()
            for row in rows:
                symbol = str(row[0])
                first, last, count = result[symbol]
                shard_first, shard_last, shard_count = int(row[1]), int(row[2]), int(row[3])
                result[symbol] = (
                    shard_first if first is None else min(first, shard_first),
                    shard_last if last is None else max(last, shard_last),
                    count + shard_count,
                )
        return result

    def date_stats(
        self, period: str, symbol: str, start_date: int, end_date: int
    ) -> tuple[int | None, int | None, int]:
        """Return stored date bounds/count inside one requested resume window."""
        if period == "1d":
            paths = [self.shards.daily_path]
        elif period == "1m":
            paths = self.shards.minute_paths_between([symbol], start_date, end_date)
        else:
            raise ValueError(f"unsupported bar period: {period}")
        first: int | None = None
        last: int | None = None
        count = 0
        for path in paths:
            if not path.exists():
                continue
            with connect_sqlite(path, readonly=True) as connection:
                row = connection.execute(
                    "SELECT min(trade_date), max(trade_date), count(*) FROM bars "
                    "WHERE symbol=? AND trade_date BETWEEN ? AND ?",
                    (symbol, start_date, end_date),
                ).fetchone()
            if row is None or int(row[2]) == 0:
                continue
            shard_first = int(row[0])
            shard_last = int(row[1])
            first = shard_first if first is None else min(first, shard_first)
            last = shard_last if last is None else max(last, shard_last)
            count += int(row[2])
        return first, last, count

    def date_stats_many(
        self,
        period: str,
        symbols: list[str],
        start_date: int,
        end_date: int,
    ) -> dict[str, tuple[int | None, int | None, int]]:
        if not symbols:
            return {}
        if period == "1d":
            paths = [self.shards.daily_path]
        elif period == "1m":
            paths = self.shards.minute_paths_between(symbols, start_date, end_date)
        else:
            raise ValueError(f"unsupported bar period: {period}")
        result: dict[str, tuple[int | None, int | None, int]] = {
            symbol: (None, None, 0) for symbol in symbols
        }
        # Stay below conservative SQLite parameter limits on older vendor builds.
        for symbol_offset in range(0, len(symbols), 900):
            batch = symbols[symbol_offset : symbol_offset + 900]
            placeholders = ",".join("?" for _ in batch)
            for path in paths:
                if not path.exists():
                    continue
                with connect_sqlite(path, readonly=True) as connection:
                    rows = connection.execute(
                        "SELECT symbol, min(trade_date), max(trade_date), count(*) FROM bars "
                        f"WHERE symbol IN ({placeholders}) AND trade_date BETWEEN ? AND ? "
                        "GROUP BY symbol",
                        [*batch, start_date, end_date],
                    ).fetchall()
                for row in rows:
                    symbol = str(row[0])
                    first, last, count = result[symbol]
                    shard_first = int(row[1])
                    shard_last = int(row[2])
                    result[symbol] = (
                        shard_first if first is None else min(first, shard_first),
                        shard_last if last is None else max(last, shard_last),
                        count + int(row[3]),
                    )
        return result

    def distinct_symbols_on_date(self, period: str, trade_date: int) -> int:
        if period != "1d":
            raise ValueError("coverage currently uses the canonical 1d database")
        if not self.shards.daily_path.exists():
            return 0
        with connect_sqlite(self.shards.daily_path, readonly=True) as connection:
            return int(
                connection.execute(
                    "SELECT count(DISTINCT symbol) FROM bars WHERE trade_date=?", (trade_date,)
                ).fetchone()[0]
            )

    def delete_symbols(self, symbols: list[str]) -> int:
        values = sorted(set(symbols))
        if not values:
            return 0
        paths = [self.shards.daily_path, *sorted(self.storage.root.glob("bars_1m_*.db"))]
        deleted = 0
        for path in paths:
            if not path.exists():
                continue
            with (
                connect_sqlite(
                    path, wal=self.storage.wal, busy_timeout_ms=self.storage.busy_timeout_ms
                ) as connection,
                write_transaction(connection),
            ):
                for offset in range(0, len(values), 900):
                    batch = values[offset : offset + 900]
                    placeholders = ",".join("?" for _ in batch)
                    before = connection.total_changes
                    connection.execute(f"DELETE FROM bars WHERE symbol IN ({placeholders})", batch)
                    deleted += connection.total_changes - before
        return deleted
