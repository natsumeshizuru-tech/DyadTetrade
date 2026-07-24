from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from dyadtetrade.data.config import DataPlatformSettings
from dyadtetrade.data.sqlite import CatalogRepository, FinancialRepository
from dyadtetrade.data.sqlite.connection import connect_sqlite, integrity_check


@dataclass(frozen=True, slots=True)
class DatabaseValidation:
    path: str
    integrity: str
    duplicate_primary_keys: int
    rows: int
    distinct_symbols: int | None
    first_trade_date: int | None
    last_trade_date: int | None
    file_size: int


def validate_databases(
    settings: DataPlatformSettings, paths: list[Path] | None = None
) -> list[DatabaseValidation]:
    selected = paths or sorted(settings.storage.root.glob("*.db"))
    results: list[DatabaseValidation] = []
    for path in selected:
        results.append(_validate_database(path))
    return results


def validation_payload(
    settings: DataPlatformSettings,
    *,
    full: bool = False,
    baseline: Path | None = None,
    changed_names: set[str] | None = None,
) -> dict[str, object]:
    baseline_payload: dict[str, object] | None = None
    reused_names: set[str] = set()
    fresh_names: set[str] = set()
    if baseline is None:
        results = validate_databases(settings)
        fresh_names = {Path(result.path).name for result in results}
    else:
        baseline_payload = json.loads(baseline.read_text(encoding="utf-8"))
        raw_databases = baseline_payload.get("databases")
        if not isinstance(raw_databases, list):
            raise ValueError(f"validation baseline has no database list: {baseline}")
        prior = {
            Path(str(item["path"])).name: item
            for item in raw_databases
            if isinstance(item, dict) and "path" in item
        }
        forced = changed_names or set()
        results = []
        for path in sorted(settings.storage.root.glob("*.db")):
            old = prior.get(path.name)
            must_refresh = (
                path.name in forced
                or old is None
                or int(old.get("file_size", -1)) != path.stat().st_size
            )
            if must_refresh:
                results.append(_validate_database(path))
                fresh_names.add(path.name)
            else:
                assert old is not None
                results.append(
                    DatabaseValidation(
                        path=str(path),
                        integrity=str(old["integrity"]),
                        duplicate_primary_keys=int(old["duplicate_primary_keys"]),
                        rows=int(old["rows"]),
                        distinct_symbols=(
                            None
                            if old.get("distinct_symbols") is None
                            else int(old["distinct_symbols"])
                        ),
                        first_trade_date=(
                            None
                            if old.get("first_trade_date") is None
                            else int(old["first_trade_date"])
                        ),
                        last_trade_date=(
                            None
                            if old.get("last_trade_date") is None
                            else int(old["last_trade_date"])
                        ),
                        file_size=path.stat().st_size,
                    )
                )
                reused_names.add(path.name)
    payload: dict[str, object] = {
        "databases": [asdict(result) for result in results],
        "integrity_ok": all(result.integrity == "ok" for result in results),
        "duplicate_primary_keys": sum(result.duplicate_primary_keys for result in results),
        "database_count": len(results),
        "validation_mode": "full" if baseline is None else "baseline_plus_changed",
        "baseline_report": None if baseline is None else str(baseline),
        "fresh_databases": sorted(fresh_names),
        "reused_databases": sorted(reused_names),
    }
    if full:
        payload["daily_coverage"] = _daily_coverage(settings)
        payload["minute_shards"] = _minute_coverage(
            settings,
            baseline_payload=baseline_payload,
            fresh_names=fresh_names,
        )
        payload["financial_coverage"] = FinancialRepository(settings.storage).summary()
        payload["reference_data"] = _reference_data_coverage(settings)
    return payload


def _validate_database(path: Path) -> DatabaseValidation:
    with connect_sqlite(path, readonly=True) as connection:
        status = integrity_check(connection)
        if path.name.startswith("bars_") or path.name.startswith("ticks_"):
            rows, duplicates, symbols, first_date, last_date = _bar_counts(connection)
        elif path.name.startswith("fundamentals_"):
            rows, duplicates, symbols, first_date, last_date = _financial_counts(connection)
        else:
            rows = _catalog_rows(connection)
            duplicates = 0
            symbols = None
            first_date = None
            last_date = None
    return DatabaseValidation(
        str(path),
        status,
        duplicates,
        rows,
        symbols,
        first_date,
        last_date,
        path.stat().st_size,
    )


def _bar_counts(
    connection: sqlite3.Connection,
) -> tuple[int, int, int, int | None, int | None]:
    row = connection.execute(
        "SELECT count(*), count(DISTINCT symbol), min(trade_date), max(trade_date) FROM bars"
    ).fetchone()
    assert row is not None
    rows = int(row[0])
    duplicate = 0
    if not _has_primary_key(connection, "bars", ("symbol", "ts")):
        duplicate = int(
            connection.execute(
                "SELECT count(*) FROM ("
                "SELECT symbol, ts FROM bars GROUP BY symbol, ts HAVING count(*) > 1)"
            ).fetchone()[0]
        )
    return (
        rows,
        duplicate,
        int(row[1]),
        None if row[2] is None else int(row[2]),
        None if row[3] is None else int(row[3]),
    )


def _catalog_rows(connection: sqlite3.Connection) -> int:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return (
        int(connection.execute("SELECT count(*) FROM instruments").fetchone()[0])
        if "instruments" in tables
        else 0
    )


def _financial_counts(
    connection: sqlite3.Connection,
) -> tuple[int, int, int, int | None, int | None]:
    row = connection.execute(
        "SELECT count(*), count(DISTINCT symbol), min(NULLIF(report_date, 0)), "
        "max(NULLIF(report_date, 0)) FROM financial_records"
    ).fetchone()
    assert row is not None
    key = ("symbol", "table_name", "report_date", "announcement_date", "row_ordinal")
    duplicate = 0
    if not _has_primary_key(connection, "financial_records", key):
        duplicate = int(
            connection.execute(
                "SELECT count(*) FROM (SELECT symbol, table_name, report_date, "
                "announcement_date, row_ordinal FROM financial_records "
                "GROUP BY symbol, table_name, report_date, announcement_date, row_ordinal "
                "HAVING count(*) > 1)"
            ).fetchone()[0]
        )
    return (
        int(row[0]),
        duplicate,
        int(row[1]),
        None if row[2] is None else int(row[2]),
        None if row[3] is None else int(row[3]),
    )


def _reference_data_coverage(settings: DataPlatformSettings) -> dict[str, object]:
    catalog = CatalogRepository(settings.storage)
    if not catalog.path.exists():
        return {"available": False}
    with catalog.connect(readonly=True) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "sector_catalog" not in tables:
            return {"available": False}
        row = connection.execute("SELECT max(snapshot_date) FROM sector_catalog").fetchone()
    if row is None or row[0] is None:
        return {"available": False}
    result = catalog.reference_data_summary(int(row[0]))
    result["available"] = True
    return result


def _has_primary_key(connection: sqlite3.Connection, table: str, expected: tuple[str, ...]) -> bool:
    columns = sorted(
        (
            (int(row[5]), str(row[1]))
            for row in connection.execute(f"PRAGMA table_info({table})")
            if int(row[5]) > 0
        ),
        key=lambda item: item[0],
    )
    return tuple(name for _, name in columns) == expected


def _daily_coverage(settings: DataPlatformSettings) -> dict[str, object]:
    catalog_path = settings.storage.root / settings.storage.catalog
    daily_path = settings.storage.root / settings.storage.daily_bars
    if not catalog_path.exists() or not daily_path.exists():
        return {"available": False}
    with connect_sqlite(catalog_path, readonly=True) as catalog:
        instruments = [
            (None if row[0] is None else int(row[0]), None if row[1] is None else int(row[1]))
            for row in catalog.execute(
                "SELECT list_date, delist_date FROM instruments "
                "WHERE is_active=1 AND instrument_type='stock'"
            )
        ]
        calendar = [
            int(row[0])
            for row in catalog.execute(
                "SELECT trade_date FROM trading_calendar WHERE market='SH' AND is_open=1 "
                "ORDER BY trade_date"
            )
        ]
    with connect_sqlite(daily_path, readonly=True) as daily:
        actual = {
            int(row[0]): int(row[1])
            for row in daily.execute(
                "SELECT trade_date, count(DISTINCT symbol) FROM bars GROUP BY trade_date"
            )
        }
    observations: list[dict[str, object]] = []
    for trade_date in calendar:
        if trade_date not in actual:
            continue
        expected = sum(
            (list_date is None or list_date <= trade_date)
            and (delist_date is None or delist_date >= trade_date)
            for list_date, delist_date in instruments
        )
        covered = actual[trade_date]
        observations.append(
            {
                "trade_date": trade_date,
                "actual_symbols": covered,
                "expected_lifecycle_symbols": expected,
                "ratio": 0.0 if expected == 0 else round(covered / expected, 6),
            }
        )
    ratios = [float(str(item["ratio"])) for item in observations]
    return {
        "available": bool(observations),
        "days": len(observations),
        "minimum_ratio": None if not ratios else min(ratios),
        "mean_ratio": None if not ratios else round(sum(ratios) / len(ratios), 6),
        "below_98_percent_days": sum(ratio < 0.98 for ratio in ratios),
        "first": None if not observations else observations[0],
        "last": None if not observations else observations[-1],
    }


def _minute_coverage(
    settings: DataPlatformSettings,
    *,
    baseline_payload: dict[str, object] | None = None,
    fresh_names: set[str] | None = None,
) -> list[dict[str, object]]:
    prior: dict[str, dict[str, object]] = {}
    if baseline_payload is not None:
        raw = baseline_payload.get("minute_shards")
        if isinstance(raw, list):
            prior = {
                Path(str(item["path"])).name: item
                for item in raw
                if isinstance(item, dict) and "path" in item
            }
    results: list[dict[str, object]] = []
    for path in sorted(settings.storage.root.glob("bars_1m_*.db")):
        old = prior.get(path.name)
        if old is None or fresh_names is None or path.name in fresh_names:
            results.append(_minute_shard_summary(path))
        else:
            copied = dict(old)
            copied["path"] = str(path)
            copied["file_size"] = path.stat().st_size
            results.append(copied)
    return results


def _minute_shard_summary(path: Path) -> dict[str, object]:
    with connect_sqlite(path, readonly=True) as connection:
        row = connection.execute(
            "SELECT count(*), count(DISTINCT symbol), count(DISTINCT trade_date), "
            "min(trade_date), max(trade_date) FROM bars"
        ).fetchone()
    assert row is not None
    symbol_days = int(row[1]) * int(row[2])
    return {
        "path": str(path),
        "rows": int(row[0]),
        "distinct_symbols": int(row[1]),
        "trading_days": int(row[2]),
        "first_trade_date": None if row[3] is None else int(row[3]),
        "last_trade_date": None if row[4] is None else int(row[4]),
        "rows_per_symbol_calendar_day": (
            None if symbol_days == 0 else round(int(row[0]) / symbol_days, 3)
        ),
        "file_size": path.stat().st_size,
    }
