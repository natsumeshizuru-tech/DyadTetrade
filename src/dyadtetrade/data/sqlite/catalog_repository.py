from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dyadtetrade.data.config import StorageConfig
from dyadtetrade.data.models import CorporateActionRecord, InstrumentRecord, QualityIssueRecord
from dyadtetrade.data.sqlite.connection import connect_sqlite, write_transaction
from dyadtetrade.data.sqlite.migrations import migrate_catalog
from dyadtetrade.data.sqlite.shard_manager import ShardManager


def utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


class CatalogRepository:
    def __init__(self, storage: StorageConfig) -> None:
        self.storage = storage
        self.path = ShardManager(storage).catalog_path

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        return connect_sqlite(
            self.path,
            readonly=readonly,
            wal=self.storage.wal,
            busy_timeout_ms=self.storage.busy_timeout_ms,
        )

    def initialize(self) -> Path:
        with self.connect() as connection:
            migrate_catalog(connection)
        return self.path

    def upsert_instruments(self, instruments: Iterable[InstrumentRecord]) -> int:
        rows = list(instruments)
        if not rows:
            return 0
        now = utc_now_ms()
        with self.connect() as connection, write_transaction(connection):
            connection.executemany(
                """
                INSERT INTO instruments(
                    symbol, code, market, name, instrument_type, list_date, delist_date,
                    is_active, price_tick, volume_multiple, raw_json, source,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    code=excluded.code,
                    market=excluded.market,
                    name=coalesce(excluded.name, instruments.name),
                    instrument_type=coalesce(excluded.instrument_type, instruments.instrument_type),
                    list_date=coalesce(excluded.list_date, instruments.list_date),
                    delist_date=coalesce(excluded.delist_date, instruments.delist_date),
                    is_active=excluded.is_active,
                    price_tick=coalesce(excluded.price_tick, instruments.price_tick),
                    volume_multiple=coalesce(excluded.volume_multiple, instruments.volume_multiple),
                    raw_json=CASE
                        WHEN excluded.raw_json IS NULL OR excluded.raw_json='{}'
                        THEN instruments.raw_json ELSE excluded.raw_json END,
                    source=CASE
                        WHEN excluded.raw_json IS NULL OR excluded.raw_json='{}'
                        THEN instruments.source ELSE excluded.source END,
                    last_seen_at=excluded.last_seen_at
                """,
                [
                    (
                        row.symbol,
                        row.code,
                        row.market,
                        row.name,
                        row.instrument_type,
                        row.list_date,
                        row.delist_date,
                        int(row.is_active),
                        row.price_tick,
                        row.volume_multiple,
                        row.raw_json,
                        row.source,
                        now,
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_calendar(self, market: str, days: list[int], *, source: str) -> int:
        normalized = sorted(set(days))
        now = utc_now_ms()
        values = []
        for index, trade_date in enumerate(normalized):
            previous = normalized[index - 1] if index else None
            following = normalized[index + 1] if index + 1 < len(normalized) else None
            values.append((market, trade_date, 1, previous, following, source, now))
        with self.connect() as connection, write_transaction(connection):
            connection.executemany(
                """
                INSERT INTO trading_calendar(
                    market, trade_date, is_open, prev_trade_date, next_trade_date,
                    source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, trade_date) DO UPDATE SET
                    is_open=excluded.is_open,
                    prev_trade_date=excluded.prev_trade_date,
                    next_trade_date=excluded.next_trade_date,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                values,
            )
        return len(values)

    def open_days(self, market: str, start: int, end: int) -> list[int]:
        with self.connect(readonly=True) as connection:
            return [
                int(row[0])
                for row in connection.execute(
                    "SELECT trade_date FROM trading_calendar "
                    "WHERE market=? AND is_open=1 AND trade_date BETWEEN ? AND ? "
                    "ORDER BY trade_date",
                    (market, start, end),
                )
            ]

    def clip_to_open_days(
        self, market: str, requested_start: int, requested_end: int
    ) -> tuple[int, int]:
        days = self.open_days(market, requested_start, requested_end)
        if not days:
            raise ValueError(
                f"trading calendar contains no open days in {requested_start}-{requested_end}"
            )
        return days[0], days[-1]

    def upsert_actions(self, actions: Iterable[CorporateActionRecord]) -> int:
        rows = list(actions)
        now = utc_now_ms()
        with self.connect() as connection, write_transaction(connection):
            connection.executemany(
                """
                INSERT INTO corporate_actions(
                    symbol, action_date, interest, stock_bonus, stock_gift, allot_num,
                    allot_price, gugai, dr, raw_json, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, action_date) DO UPDATE SET
                    interest=excluded.interest,
                    stock_bonus=excluded.stock_bonus,
                    stock_gift=excluded.stock_gift,
                    allot_num=excluded.allot_num,
                    allot_price=excluded.allot_price,
                    gugai=excluded.gugai,
                    dr=excluded.dr,
                    raw_json=excluded.raw_json,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        row.symbol,
                        row.action_date,
                        row.interest,
                        row.stock_bonus,
                        row.stock_gift,
                        row.allot_num,
                        row.allot_price,
                        row.gugai,
                        row.dr,
                        row.raw_json,
                        row.source,
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_sector_snapshot(
        self,
        snapshot_date: int,
        sector_name: str,
        symbols: list[str],
        *,
        source: str,
    ) -> int:
        with self.connect() as connection, write_transaction(connection):
            connection.executemany(
                """
                INSERT INTO sector_snapshots(snapshot_date, sector_name, symbol, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_date, sector_name, symbol) DO UPDATE SET
                    source=excluded.source
                """,
                [(snapshot_date, sector_name, symbol, source) for symbol in symbols],
            )
        return len(symbols)

    def replace_sector_snapshots(
        self,
        snapshot_date: int,
        snapshots: Iterable[tuple[str, list[str], int]],
        *,
        source: str,
    ) -> tuple[int, int]:
        rows = list(snapshots)
        if not rows:
            return 0, 0
        now = utc_now_ms()
        membership_count = 0
        with self.connect() as connection, write_transaction(connection):
            for sector_name, symbols, total_member_count in rows:
                normalized = sorted(set(symbols))
                connection.execute(
                    "DELETE FROM sector_snapshots WHERE snapshot_date=? AND sector_name=?",
                    (snapshot_date, sector_name),
                )
                connection.executemany(
                    "INSERT INTO sector_snapshots(snapshot_date, sector_name, symbol, source) "
                    "VALUES (?, ?, ?, ?)",
                    [(snapshot_date, sector_name, symbol, source) for symbol in normalized],
                )
                connection.execute(
                    """
                    INSERT INTO sector_catalog(
                        snapshot_date, sector_name, total_member_count,
                        a_share_member_count, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(snapshot_date, sector_name) DO UPDATE SET
                        total_member_count=excluded.total_member_count,
                        a_share_member_count=excluded.a_share_member_count,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    (
                        snapshot_date,
                        sector_name,
                        total_member_count,
                        len(normalized),
                        source,
                        now,
                    ),
                )
                membership_count += len(normalized)
        return len(rows), membership_count

    def replace_index_weight_snapshots(
        self,
        snapshot_date: int,
        snapshots: Iterable[tuple[str, dict[str, float], int]],
        *,
        source: str,
    ) -> tuple[int, int]:
        rows = list(snapshots)
        if not rows:
            return 0, 0
        now = utc_now_ms()
        component_count = 0
        with self.connect() as connection, write_transaction(connection):
            for index_code, weights, total_component_count in rows:
                normalized = dict(sorted(weights.items()))
                connection.execute(
                    "DELETE FROM index_weight_snapshots WHERE snapshot_date=? AND index_code=?",
                    (snapshot_date, index_code),
                )
                connection.executemany(
                    "INSERT INTO index_weight_snapshots("
                    "snapshot_date, index_code, symbol, weight, source"
                    ") VALUES (?, ?, ?, ?, ?)",
                    [
                        (snapshot_date, index_code, symbol, weight, source)
                        for symbol, weight in normalized.items()
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO index_weight_catalog(
                        snapshot_date, index_code, total_component_count,
                        a_share_component_count, total_weight, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(snapshot_date, index_code) DO UPDATE SET
                        total_component_count=excluded.total_component_count,
                        a_share_component_count=excluded.a_share_component_count,
                        total_weight=excluded.total_weight,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    (
                        snapshot_date,
                        index_code,
                        total_component_count,
                        len(normalized),
                        sum(normalized.values()),
                        source,
                        now,
                    ),
                )
                component_count += len(normalized)
        return len(rows), component_count

    def reference_data_summary(self, snapshot_date: int) -> dict[str, object]:
        with self.connect(readonly=True) as connection:
            sector_row = connection.execute(
                "SELECT count(*), coalesce(sum(a_share_member_count), 0), "
                "coalesce(sum(total_member_count), 0) FROM sector_catalog "
                "WHERE snapshot_date=?",
                (snapshot_date,),
            ).fetchone()
            weight_row = connection.execute(
                "SELECT count(*), coalesce(sum(a_share_component_count), 0), "
                "coalesce(sum(total_component_count), 0) FROM index_weight_catalog "
                "WHERE snapshot_date=?",
                (snapshot_date,),
            ).fetchone()
            sectors_with_a_shares = int(
                connection.execute(
                    "SELECT count(*) FROM sector_catalog WHERE snapshot_date=? "
                    "AND a_share_member_count > 0",
                    (snapshot_date,),
                ).fetchone()[0]
            )
            indices_with_weights = int(
                connection.execute(
                    "SELECT count(*) FROM index_weight_catalog WHERE snapshot_date=? "
                    "AND a_share_component_count > 0",
                    (snapshot_date,),
                ).fetchone()[0]
            )
        return {
            "snapshot_date": snapshot_date,
            "sector_count": int(sector_row[0]),
            "sectors_with_a_shares": sectors_with_a_shares,
            "a_share_sector_memberships": int(sector_row[1]),
            "all_sector_memberships": int(sector_row[2]),
            "index_count": int(weight_row[0]),
            "indices_with_weights": indices_with_weights,
            "a_share_index_components": int(weight_row[1]),
            "all_index_components": int(weight_row[2]),
        }

    def sector_names_on_snapshot(self, snapshot_date: int) -> set[str]:
        with self.connect(readonly=True) as connection:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT sector_name FROM sector_catalog WHERE snapshot_date=?",
                    (snapshot_date,),
                )
            }

    def index_codes_on_snapshot(self, snapshot_date: int) -> set[str]:
        with self.connect(readonly=True) as connection:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT index_code FROM index_weight_catalog WHERE snapshot_date=?",
                    (snapshot_date,),
                )
            }

    def all_instrument_symbols(self, instrument_types: tuple[str, ...] | None = None) -> list[str]:
        with self.connect(readonly=True) as connection:
            if instrument_types:
                placeholders = ",".join("?" for _ in instrument_types)
                rows = connection.execute(
                    f"SELECT symbol FROM instruments WHERE instrument_type IN ({placeholders}) "
                    "ORDER BY symbol",
                    instrument_types,
                )
            else:
                rows = connection.execute("SELECT symbol FROM instruments ORDER BY symbol")
            return [str(row[0]) for row in rows]

    def active_stock_symbols(self) -> list[str]:
        with self.connect(readonly=True) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT symbol FROM instruments "
                    "WHERE is_active=1 AND instrument_type='stock' ORDER BY symbol"
                )
            ]

    def stock_symbols_for_scope(self, start: int, end: int) -> list[str]:
        """Return stocks whose recorded listing lifecycle intersects the scope."""
        with self.connect(readonly=True) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT symbol FROM instruments WHERE instrument_type='stock' "
                    "AND (list_date IS NULL OR list_date<=?) "
                    "AND (delist_date IS NULL OR delist_date>=?) ORDER BY symbol",
                    (end, start),
                )
            ]

    def active_symbols(self, instrument_types: tuple[str, ...] = ("stock",)) -> list[str]:
        if not instrument_types:
            return []
        placeholders = ",".join("?" for _ in instrument_types)
        with self.connect(readonly=True) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    f"SELECT symbol FROM instruments WHERE is_active=1 "
                    f"AND instrument_type IN ({placeholders}) ORDER BY symbol",
                    instrument_types,
                )
            ]

    def completed_symbols_for_scope(
        self,
        *,
        dataset: str,
        period: str,
        start: int,
        end: int,
    ) -> set[str]:
        """Return symbols covered by a completed item spanning the requested scope.

        Row presence is not a reliable completion marker for sparse datasets such as
        fundamentals: a valid table can have no row in a calendar year.  Job-item scope
        records both successful empty results and populated results, so it is the durable
        resume checkpoint.
        """
        with self.connect(readonly=True) as connection:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT symbol FROM job_items "
                    "WHERE dataset=? AND period=? AND status='completed' "
                    "AND effective_start<=? AND effective_end>=?",
                    (dataset, period, start, end),
                )
            }

    def instrument_lifecycle(self, symbol: str) -> tuple[int | None, int | None]:
        """Return the inclusive listing/delisting dates recorded for a symbol."""
        with self.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT list_date, delist_date FROM instruments WHERE symbol=?", (symbol,)
            ).fetchone()
        if row is None:
            return None, None
        list_date = None if row[0] is None else int(row[0])
        delist_date = None if row[1] is None else int(row[1])
        return list_date, delist_date

    def instrument_lifecycles(
        self, symbols: Iterable[str]
    ) -> dict[str, tuple[int | None, int | None]]:
        values = list(symbols)
        result: dict[str, tuple[int | None, int | None]] = {
            symbol: (None, None) for symbol in values
        }
        with self.connect(readonly=True) as connection:
            for offset in range(0, len(values), 900):
                batch = values[offset : offset + 900]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                for row in connection.execute(
                    "SELECT symbol, list_date, delist_date FROM instruments "
                    f"WHERE symbol IN ({placeholders})",
                    batch,
                ):
                    result[str(row[0])] = (
                        None if row[1] is None else int(row[1]),
                        None if row[2] is None else int(row[2]),
                    )
        return result

    def active_instrument_count(self) -> int:
        with self.connect(readonly=True) as connection:
            return int(
                connection.execute("SELECT count(*) FROM instruments WHERE is_active=1").fetchone()[
                    0
                ]
            )

    def mark_out_of_scope(self, symbols: Iterable[str], *, reason: str) -> int:
        values = sorted(set(symbols))
        if not values:
            return 0
        with self.connect() as connection, write_transaction(connection):
            for offset in range(0, len(values), 900):
                batch = values[offset : offset + 900]
                placeholders = ",".join("?" for _ in batch)
                connection.execute(
                    f"UPDATE instruments SET is_active=0, instrument_type=? "
                    f"WHERE symbol IN ({placeholders})",
                    [reason, *batch],
                )
                connection.execute(
                    f"DELETE FROM corporate_actions WHERE symbol IN ({placeholders})", batch
                )
                connection.execute(
                    f"DELETE FROM dataset_watermarks WHERE symbol IN ({placeholders})", batch
                )
        return len(values)

    def record_shard(
        self,
        path: Path,
        *,
        dataset: str,
        market: str | None,
        period: str,
        start_date: int | None,
        end_date: int | None,
        row_count: int,
        writable: bool,
        checksum: str | None,
    ) -> None:
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                """
                INSERT INTO shard_manifest(
                    shard_path, dataset, market, period, start_date, end_date, row_count,
                    file_size, schema_version, writable, checksum, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(shard_path) DO UPDATE SET
                    row_count=excluded.row_count,
                    file_size=excluded.file_size,
                    writable=excluded.writable,
                    checksum=excluded.checksum,
                    updated_at=excluded.updated_at
                """,
                (
                    str(path.resolve()),
                    dataset,
                    market,
                    period,
                    start_date,
                    end_date,
                    row_count,
                    path.stat().st_size,
                    int(writable),
                    checksum,
                    utc_now_ms(),
                ),
            )

    def record_archive(
        self,
        *,
        source_path: Path,
        archive_path: Path,
        dataset: str,
        market: str | None,
        period: str,
        start_date: int | None,
        end_date: int | None,
        row_count: int,
        file_size: int,
        sha256: str,
        schema_version: int,
    ) -> None:
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                """
                INSERT INTO archive_manifest(
                    source_path, archive_path, dataset, market, period, start_date,
                    end_date, row_count, file_size, sha256, schema_version, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(archive_path) DO UPDATE SET
                    row_count=excluded.row_count,
                    file_size=excluded.file_size,
                    sha256=excluded.sha256,
                    verified_at=excluded.verified_at
                """,
                (
                    str(source_path.resolve()),
                    str(archive_path.resolve()),
                    dataset,
                    market,
                    period,
                    start_date,
                    end_date,
                    row_count,
                    file_size,
                    sha256,
                    schema_version,
                    utc_now_ms(),
                    utc_now_ms(),
                ),
            )

    def corporate_actions(self, symbols: list[str], start: int, end: int) -> list[sqlite3.Row]:
        if not symbols:
            return []
        placeholders = ",".join("?" for _ in symbols)
        with self.connect(readonly=True) as connection:
            return list(
                connection.execute(
                    "SELECT ca.* FROM corporate_actions AS ca "
                    "LEFT JOIN instruments AS i ON i.symbol=ca.symbol "
                    f"WHERE ca.symbol IN ({placeholders}) "
                    "AND ca.action_date BETWEEN ? AND ? "
                    "AND (i.list_date IS NULL OR ca.action_date>=i.list_date) "
                    "ORDER BY ca.symbol, ca.action_date",
                    [*symbols, start, end],
                )
            )

    def sector_symbols(self, sector_name: str, *, snapshot_date: int | None = None) -> list[str]:
        with self.connect(readonly=True) as connection:
            effective_date = snapshot_date
            if effective_date is None:
                row = connection.execute(
                    "SELECT max(snapshot_date) FROM sector_snapshots WHERE sector_name=?",
                    (sector_name,),
                ).fetchone()
                effective_date = None if row is None or row[0] is None else int(row[0])
            if effective_date is None:
                return []
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT symbol FROM sector_snapshots "
                    "WHERE sector_name=? AND snapshot_date=? ORDER BY symbol",
                    (sector_name, effective_date),
                )
            ]

    def instrument(self, symbol: str) -> dict[str, Any] | None:
        with self.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT * FROM instruments WHERE symbol=?", (symbol,)
            ).fetchone()
            return None if row is None else dict(row)

    def create_job(
        self,
        job_id: str,
        job_type: str,
        *,
        requested_start: int | None,
        requested_end: int | None,
        effective_start: int | None,
        effective_end: int | None,
    ) -> None:
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                """
                INSERT INTO job_runs(
                    job_id, job_type, requested_start, requested_end, effective_start,
                    effective_end, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                ON CONFLICT(job_id) DO NOTHING
                """,
                (
                    job_id,
                    job_type,
                    requested_start,
                    requested_end,
                    effective_start,
                    effective_end,
                    utc_now_ms(),
                ),
            )

    def finish_job(
        self,
        job_id: str,
        *,
        status: str,
        total_items: int,
        success_items: int,
        failed_items: int,
        inserted_rows: int,
        updated_rows: int,
        summary: dict[str, Any],
        error_text: str | None = None,
    ) -> None:
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                """
                UPDATE job_runs SET status=?, finished_at=?, total_items=?, success_items=?,
                    failed_items=?, inserted_rows=?, updated_rows=?, summary_json=?, error_text=?
                WHERE job_id=?
                """,
                (
                    status,
                    utc_now_ms(),
                    total_items,
                    success_items,
                    failed_items,
                    inserted_rows,
                    updated_rows,
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    error_text,
                    job_id,
                ),
            )

    def mark_job_interrupted(self, job_id: str, *, reason: str) -> None:
        finished_at = utc_now_ms()
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                """
                UPDATE job_items SET status='interrupted', error_class='InterruptedError',
                    error_text=?, finished_at=? WHERE job_id=? AND status='running'
                """,
                (reason, finished_at, job_id),
            )
            counts = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT status, count(*) FROM job_items WHERE job_id=? GROUP BY status",
                    (job_id,),
                )
            }
            connection.execute(
                """
                UPDATE job_runs SET status='interrupted', finished_at=?, total_items=?,
                    success_items=?, failed_items=?, error_text=? WHERE job_id=?
                """,
                (
                    finished_at,
                    sum(counts.values()),
                    counts.get("completed", 0),
                    counts.get("failed", 0) + counts.get("interrupted", 0),
                    reason,
                    job_id,
                ),
            )

    def start_job_item(
        self,
        job_id: str,
        *,
        dataset: str,
        period: str,
        symbol: str,
        requested_start: int,
        requested_end: int,
        effective_start: int,
        effective_end: int,
    ) -> int:
        now = utc_now_ms()
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                """
                INSERT INTO job_items(
                    job_id, dataset, period, symbol, requested_start, requested_end,
                    effective_start, effective_end, status, attempts, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', 1, ?)
                ON CONFLICT(job_id, dataset, period, symbol, effective_start, effective_end)
                DO UPDATE SET status='running', attempts=job_items.attempts + 1,
                    started_at=excluded.started_at, finished_at=NULL,
                    error_class=NULL, error_text=NULL
                """,
                (
                    job_id,
                    dataset,
                    period,
                    symbol,
                    requested_start,
                    requested_end,
                    effective_start,
                    effective_end,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT item_id FROM job_items WHERE job_id=? AND dataset=? AND period=?
                    AND symbol=? AND effective_start=? AND effective_end=?
                """,
                (job_id, dataset, period, symbol, effective_start, effective_end),
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to create job item")
            return int(row[0])

    def finish_job_item(
        self,
        item_id: int,
        *,
        status: str,
        inserted_rows: int = 0,
        updated_rows: int = 0,
        error: BaseException | None = None,
    ) -> None:
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                """
                UPDATE job_items SET status=?, inserted_rows=?, updated_rows=?,
                    error_class=?, error_text=?, finished_at=? WHERE item_id=?
                """,
                (
                    status,
                    inserted_rows,
                    updated_rows,
                    None if error is None else type(error).__name__,
                    None if error is None else str(error),
                    utc_now_ms(),
                    item_id,
                ),
            )

    def finish_job_items_completed(self, items: Iterable[tuple[int, int, int]]) -> None:
        rows = list(items)
        if not rows:
            return
        finished_at = utc_now_ms()
        with self.connect() as connection, write_transaction(connection):
            connection.executemany(
                """
                UPDATE job_items SET status='completed', inserted_rows=?, updated_rows=?,
                    error_class=NULL, error_text=NULL, finished_at=? WHERE item_id=?
                """,
                [
                    (inserted_rows, updated_rows, finished_at, item_id)
                    for item_id, inserted_rows, updated_rows in rows
                ],
            )

    def update_watermark(
        self,
        *,
        dataset: str,
        period: str,
        symbol: str,
        first_ts: int | None,
        last_ts: int | None,
        row_count: int,
        job_id: str,
    ) -> None:
        self.update_watermarks(
            [(period, symbol, first_ts, last_ts, row_count)],
            dataset=dataset,
            job_id=job_id,
        )

    def update_watermarks(
        self,
        watermarks: Iterable[tuple[str, str, int | None, int | None, int]],
        *,
        dataset: str,
        job_id: str,
    ) -> None:
        rows = list(watermarks)
        if not rows:
            return
        updated_at = utc_now_ms()
        with self.connect() as connection, write_transaction(connection):
            connection.executemany(
                """
                INSERT INTO dataset_watermarks(
                    dataset, period, symbol, first_ts, last_ts, row_count,
                    last_success_job_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset, period, symbol) DO UPDATE SET
                    first_ts=CASE
                        WHEN dataset_watermarks.first_ts IS NULL THEN excluded.first_ts
                        WHEN excluded.first_ts IS NULL THEN dataset_watermarks.first_ts
                        ELSE min(dataset_watermarks.first_ts, excluded.first_ts)
                    END,
                    last_ts=CASE
                        WHEN dataset_watermarks.last_ts IS NULL THEN excluded.last_ts
                        WHEN excluded.last_ts IS NULL THEN dataset_watermarks.last_ts
                        ELSE max(dataset_watermarks.last_ts, excluded.last_ts)
                    END,
                    row_count=excluded.row_count,
                    last_success_job_id=excluded.last_success_job_id,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        dataset,
                        period,
                        symbol,
                        first_ts,
                        last_ts,
                        row_count,
                        job_id,
                        updated_at,
                    )
                    for period, symbol, first_ts, last_ts, row_count in rows
                ],
            )

    def record_issues(self, issues: Iterable[QualityIssueRecord]) -> int:
        rows = list(issues)
        with self.connect() as connection, write_transaction(connection):
            connection.executemany(
                """
                INSERT INTO data_quality_issues(
                    severity, symbol, period, trade_date, rule, details_json,
                    job_id, shard, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.severity,
                        row.symbol,
                        row.period,
                        row.trade_date,
                        row.rule,
                        json.dumps(row.details, ensure_ascii=False, sort_keys=True),
                        row.job_id,
                        row.shard,
                        utc_now_ms(),
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def resolve_open_errors(
        self, symbol: str, period: str, *, except_job_id: str | None = None
    ) -> int:
        """Resolve superseded hard failures after a clean retry for the same dataset item."""
        parameters: list[object] = [utc_now_ms(), symbol, period]
        condition = ""
        if except_job_id is not None:
            condition = " AND (job_id IS NULL OR job_id<>?)"
            parameters.append(except_job_id)
        with self.connect() as connection, write_transaction(connection):
            cursor = connection.execute(
                "UPDATE data_quality_issues SET resolved_at=? "
                "WHERE symbol=? AND period=? AND severity='error' "
                f"AND resolved_at IS NULL{condition}",
                parameters,
            )
        return int(cursor.rowcount)

    def resolve_open_issues(
        self,
        symbol: str,
        period: str,
        *,
        rules: tuple[str, ...],
        except_job_id: str | None = None,
    ) -> int:
        """Resolve superseded warnings/errors when a more precise retry result exists."""
        if not rules:
            return 0
        placeholders = ",".join("?" for _ in rules)
        parameters: list[object] = [utc_now_ms(), symbol, period, *rules]
        condition = ""
        if except_job_id is not None:
            condition = " AND (job_id IS NULL OR job_id<>?)"
            parameters.append(except_job_id)
        with self.connect() as connection, write_transaction(connection):
            cursor = connection.execute(
                "UPDATE data_quality_issues SET resolved_at=? "
                "WHERE symbol=? AND period=? AND resolved_at IS NULL "
                f"AND rule IN ({placeholders}){condition}",
                parameters,
            )
        return int(cursor.rowcount)

    def acquire_lock(self, lock_name: str, owner_id: str, *, ttl_seconds: int) -> bool:
        now = utc_now_ms()
        expires = now + ttl_seconds * 1000
        with self.connect() as connection, write_transaction(connection):
            connection.execute("DELETE FROM process_locks WHERE expires_at < ?", (now,))
            cursor = connection.execute(
                "INSERT OR IGNORE INTO process_locks VALUES (?, ?, ?, ?, ?)",
                (lock_name, owner_id, now, now, expires),
            )
            return cursor.rowcount == 1

    def release_lock(self, lock_name: str, owner_id: str) -> None:
        with self.connect() as connection, write_transaction(connection):
            connection.execute(
                "DELETE FROM process_locks WHERE lock_name=? AND owner_id=?",
                (lock_name, owner_id),
            )
