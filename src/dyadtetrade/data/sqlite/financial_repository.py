from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from dyadtetrade.data.config import StorageConfig
from dyadtetrade.data.models import FinancialRecord
from dyadtetrade.data.sqlite.connection import connect_sqlite, write_transaction
from dyadtetrade.data.sqlite.migrations import migrate_fundamentals
from dyadtetrade.data.sqlite.shard_manager import ShardManager


@dataclass(frozen=True, slots=True)
class FinancialUpsertResult:
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int
    deleted_rows: int
    shards: tuple[Path, ...]


class FinancialRepository:
    def __init__(self, storage: StorageConfig) -> None:
        self.storage = storage
        self.shards = ShardManager(storage)

    def connect(self, path: Path, *, readonly: bool = False) -> sqlite3.Connection:
        return connect_sqlite(
            path,
            readonly=readonly,
            wal=self.storage.wal,
            busy_timeout_ms=self.storage.busy_timeout_ms,
        )

    def initialize(self, years: list[int]) -> tuple[Path, ...]:
        paths = tuple(self.shards.fundamentals_path(year) for year in sorted(set(years)))
        for path in paths:
            with self.connect(path) as connection:
                migrate_fundamentals(connection)
        return paths

    def upsert(self, records: list[FinancialRecord]) -> FinancialUpsertResult:
        grouped: defaultdict[Path, list[FinancialRecord]] = defaultdict(list)
        for record in records:
            grouped[self._path_for(record)].append(record)
        inserted = updated = unchanged = deleted = 0
        for path, rows in grouped.items():
            with self.connect(path) as connection:
                migrate_fundamentals(connection)
                existing = self._existing_hashes(connection, rows)
                incoming_keys = {self._key(row) for row in rows}
                refreshed_groups = {
                    (row.symbol, row.table_name, row.report_date, row.announcement_date)
                    for row in rows
                }
                obsolete_keys = [
                    key
                    for key in existing
                    if key[:4] in refreshed_groups and key not in incoming_keys
                ]
                deleted += len(obsolete_keys)
                for row in rows:
                    key = self._key(row)
                    old_hash = existing.get(key)
                    if old_hash is None:
                        inserted += 1
                    elif old_hash == row.source_hash:
                        unchanged += 1
                    else:
                        updated += 1
                with write_transaction(connection):
                    connection.executemany(
                        "DELETE FROM financial_records WHERE symbol=? AND table_name=? "
                        "AND report_date=? AND announcement_date=? AND row_ordinal=?",
                        obsolete_keys,
                    )
                    connection.executemany(
                        """
                        INSERT INTO financial_records(
                            symbol, table_name, report_date, announcement_date,
                            row_ordinal, raw_json, source, ingested_at, source_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(
                            symbol, table_name, report_date, announcement_date, row_ordinal
                        ) DO UPDATE SET
                            raw_json=excluded.raw_json,
                            source=excluded.source,
                            ingested_at=excluded.ingested_at,
                            source_hash=excluded.source_hash
                        WHERE financial_records.source_hash IS NOT excluded.source_hash
                        """,
                        [row.sqlite_values() for row in rows],
                    )
        return FinancialUpsertResult(
            inserted_rows=inserted,
            updated_rows=updated,
            unchanged_rows=unchanged,
            deleted_rows=deleted,
            shards=tuple(sorted(grouped)),
        )

    def summary(self, paths: list[Path] | None = None) -> dict[str, object]:
        selected = paths or sorted(self.storage.root.glob("fundamentals_*.db"))
        rows_by_table: dict[str, int] = defaultdict(int)
        symbols_by_table: dict[str, set[str]] = defaultdict(set)
        rows_by_year: dict[str, int] = {}
        first_report: int | None = None
        last_report: int | None = None
        last_announcement: int | None = None
        total = 0
        for path in selected:
            if not path.exists():
                continue
            with self.connect(path, readonly=True) as connection:
                count_row = connection.execute("SELECT count(*) FROM financial_records").fetchone()
                count = int(count_row[0])
                total += count
                rows_by_year[path.stem.rsplit("_", 1)[-1]] = count
                date_row = connection.execute(
                    "SELECT min(NULLIF(report_date, 0)), max(NULLIF(report_date, 0)), "
                    "max(NULLIF(announcement_date, 0)) FROM financial_records"
                ).fetchone()
                if date_row[0] is not None:
                    first_report = (
                        int(date_row[0])
                        if first_report is None
                        else min(first_report, int(date_row[0]))
                    )
                if date_row[1] is not None:
                    last_report = (
                        int(date_row[1])
                        if last_report is None
                        else max(last_report, int(date_row[1]))
                    )
                if date_row[2] is not None:
                    last_announcement = (
                        int(date_row[2])
                        if last_announcement is None
                        else max(last_announcement, int(date_row[2]))
                    )
                for row in connection.execute(
                    "SELECT table_name, count(*), count(DISTINCT symbol) "
                    "FROM financial_records GROUP BY table_name"
                ):
                    table = str(row[0])
                    rows_by_table[table] += int(row[1])
                for row in connection.execute(
                    "SELECT DISTINCT table_name, symbol FROM financial_records"
                ):
                    symbols_by_table[str(row[0])].add(str(row[1]))
        return {
            "total_rows": total,
            "rows_by_year": dict(sorted(rows_by_year.items())),
            "rows_by_table": dict(sorted(rows_by_table.items())),
            "symbols_by_table": {
                key: len(value) for key, value in sorted(symbols_by_table.items())
            },
            "first_report_date": first_report,
            "last_report_date": last_report,
            "last_announcement_date": last_announcement,
        }

    def symbols_with_tables(self, tables: list[str], *, start_year: int, end_year: int) -> set[str]:
        required = set(tables)
        observed: dict[str, set[str]] = defaultdict(set)
        for year in range(start_year, end_year + 1):
            path = self.shards.fundamentals_path(year)
            if not path.exists():
                continue
            with self.connect(path, readonly=True) as connection:
                for row in connection.execute(
                    "SELECT DISTINCT symbol, table_name FROM financial_records"
                ):
                    observed[str(row[0])].add(str(row[1]))
        return {symbol for symbol, names in observed.items() if required <= names}

    def delete_out_of_scope(self, *, start: int, end: int) -> int:
        deleted = 0
        for year in range(start // 10_000, end // 10_000 + 1):
            path = self.shards.fundamentals_path(year)
            if not path.exists():
                continue
            with self.connect(path) as connection, write_transaction(connection):
                before = connection.total_changes
                connection.execute(
                    "DELETE FROM financial_records WHERE "
                    "(report_date <> 0 AND report_date NOT BETWEEN ? AND ?) OR "
                    "(report_date = 0 AND announcement_date NOT BETWEEN ? AND ?) OR "
                    "announcement_date > ?",
                    (start, end, start, end, end),
                )
                deleted += connection.total_changes - before
        return deleted

    def _path_for(self, record: FinancialRecord) -> Path:
        dated = record.report_date or record.announcement_date
        if dated <= 0:
            raise ValueError("financial record has neither report nor announcement date")
        return self.shards.fundamentals_path(dated // 10_000)

    @staticmethod
    def _key(record: FinancialRecord) -> tuple[str, str, int, int, int]:
        return (
            record.symbol,
            record.table_name,
            record.report_date,
            record.announcement_date,
            record.row_ordinal,
        )

    def _existing_hashes(
        self, connection: sqlite3.Connection, rows: list[FinancialRecord]
    ) -> dict[tuple[str, str, int, int, int], str]:
        result: dict[tuple[str, str, int, int, int], str] = {}
        symbols = sorted({row.symbol for row in rows})
        for offset in range(0, len(symbols), 900):
            batch = symbols[offset : offset + 900]
            placeholders = ",".join("?" for _ in batch)
            for row in connection.execute(
                "SELECT symbol, table_name, report_date, announcement_date, "
                "row_ordinal, source_hash FROM financial_records "
                f"WHERE symbol IN ({placeholders})",
                batch,
            ):
                result[(str(row[0]), str(row[1]), int(row[2]), int(row[3]), int(row[4]))] = str(
                    row[5]
                )
        return result
