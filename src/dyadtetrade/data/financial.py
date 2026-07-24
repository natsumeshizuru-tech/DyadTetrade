from __future__ import annotations

import hashlib
import json
import math
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from dyadtetrade.data.config import DataPlatformSettings
from dyadtetrade.data.gateway import DataGatewayError, MarketDataGateway
from dyadtetrade.data.models import FinancialRecord
from dyadtetrade.data.sqlite import CatalogRepository, FinancialRepository
from dyadtetrade.data.sqlite.catalog_repository import utc_now_ms
from dyadtetrade.data.sqlite.connection import connect_sqlite, integrity_check

CHINA_TZ = ZoneInfo("Asia/Shanghai")
REPORT_DATE_FIELDS = ("m_timetag", "endDate")
ANNOUNCEMENT_DATE_FIELDS = ("m_anntime", "declareDate")


@dataclass(frozen=True, slots=True)
class FinancialBackfillSummary:
    job_id: str
    requested_start: int
    requested_end: int
    symbol_count: int
    success_items: int
    failed_items: int
    empty_symbols: int
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int
    deleted_rows: int
    shards: tuple[str, ...]
    coverage: dict[str, object]
    elapsed_seconds: float
    report_json: str
    report_text: str


def normalize_financial_frames(
    symbol: str,
    frames_by_mode: list[dict[str, pd.DataFrame]],
    *,
    tables: list[str],
    start: int,
    end: int,
    source: str,
    ingested_at: int | None = None,
) -> list[FinancialRecord]:
    """Union report/announcement queries and preserve every upstream field as JSON."""
    observed: dict[str, dict[tuple[int, int], set[str]]] = {
        table: defaultdict(set) for table in tables
    }
    for mode_frames in frames_by_mode:
        for table in tables:
            frame = mode_frames.get(table)
            if frame is None or frame.empty:
                continue
            for raw in frame.to_dict(orient="records"):
                cleaned = {str(key): _clean_value(value) for key, value in raw.items()}
                quarter = cleaned.get("m_quarter")
                if quarter is not None and quarter not in {1, 2, 3, 4}:
                    # The current QMT Capital decoder intermittently exposes uninitialized
                    # integers here. Invalid quarter tags must not create phantom revisions.
                    cleaned["m_quarter"] = None
                report_date = _first_date(cleaned, REPORT_DATE_FIELDS)
                announcement_date = _first_date(cleaned, ANNOUNCEMENT_DATE_FIELDS)
                # The requested interval is fundamentally a report-period scope. A row with
                # no report date can still be retained by its announcement date.
                period_in_scope = start <= report_date <= end or (
                    report_date == 0 and start <= announcement_date <= end
                )
                available_by_end = announcement_date == 0 or announcement_date <= end
                if not period_in_scope or not available_by_end:
                    continue
                raw_json = json.dumps(
                    cleaned,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                observed[table][(report_date, announcement_date)].add(raw_json)

    timestamp = utc_now_ms() if ingested_at is None else ingested_at
    result: list[FinancialRecord] = []
    for table in tables:
        for (report_date, announcement_date), payloads in sorted(observed[table].items()):
            for ordinal, raw_json in enumerate(sorted(payloads)):
                digest = hashlib.blake2b(
                    f"{symbol}|{table}|{raw_json}".encode(), digest_size=16
                ).hexdigest()
                result.append(
                    FinancialRecord(
                        symbol=symbol.upper(),
                        table_name=table,
                        report_date=report_date,
                        announcement_date=announcement_date,
                        row_ordinal=ordinal,
                        raw_json=raw_json,
                        source=source,
                        ingested_at=timestamp,
                        source_hash=digest,
                    )
                )
    return result


class FinancialBackfillRunner:
    def __init__(self, settings: DataPlatformSettings, gateway: MarketDataGateway) -> None:
        self.settings = settings
        self.gateway = gateway
        self.catalog = CatalogRepository(settings.storage)
        self.financial = FinancialRepository(settings.storage)

    def run(
        self,
        *,
        start: int,
        end: int,
        symbols_override: list[str] | None = None,
        resume: bool = False,
    ) -> FinancialBackfillSummary:
        started = time.monotonic()
        self.catalog.initialize()
        self.financial.initialize(list(range(start // 10_000, end // 10_000 + 1)))
        deleted_out_of_scope = self.financial.delete_out_of_scope(start=start, end=end)
        # Fundamentals remain available after delisting, so use the historical
        # lifecycle universe rather than today's active stock list.
        symbols = self.catalog.stock_symbols_for_scope(start, end)
        if symbols_override:
            selected = {item.upper() for item in symbols_override}
            unknown = selected - set(symbols)
            if unknown:
                raise ValueError(
                    "requested symbols are outside the A-share lifecycle scope: "
                    + ", ".join(sorted(unknown))
                )
            symbols = sorted(selected)
        tables = list(dict.fromkeys(self.settings.financial.tables))
        if resume:
            fully_completed = self.catalog.completed_symbols_for_scope(
                dataset="fundamentals",
                period="financial",
                start=start,
                end=end,
            )
            symbols = [symbol for symbol in symbols if symbol not in fully_completed]
        job_id = f"financial-backfill-{datetime.now(CHINA_TZ):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        self.catalog.create_job(
            job_id,
            "financial-backfill",
            requested_start=start,
            requested_end=end,
            effective_start=start,
            effective_end=end,
        )
        success = failed = empty = inserted = updated = unchanged = 0
        deleted = deleted_out_of_scope
        shard_paths: set[Path] = set()
        errors: list[dict[str, str]] = []
        batches = _batches(symbols, self.settings.financial.download_batch_symbols)
        for batch_index, batch in enumerate(batches, start=1):
            item_ids = {
                symbol: self.catalog.start_job_item(
                    job_id,
                    dataset="fundamentals",
                    period="financial",
                    symbol=symbol,
                    requested_start=start,
                    requested_end=end,
                    effective_start=start,
                    effective_end=end,
                )
                for symbol in batch
            }
            downloaded, download_errors = self._download_isolated(
                batch, tables=tables, start=start, end=end
            )
            for symbol, error in download_errors.items():
                failed += 1
                errors.append({"symbol": symbol, "stage": "download", "error": str(error)})
                self.catalog.finish_job_item(item_ids[symbol], status="failed", error=error)
            frames, read_errors = self._read_isolated(
                downloaded, tables=tables, start=start, end=end
            )
            for symbol, error in read_errors.items():
                failed += 1
                errors.append({"symbol": symbol, "stage": "read", "error": str(error)})
                self.catalog.finish_job_item(item_ids[symbol], status="failed", error=error)
            records: list[FinancialRecord] = []
            completed: list[str] = []
            for symbol in downloaded:
                if symbol in read_errors:
                    continue
                symbol_records = normalize_financial_frames(
                    symbol,
                    frames.get(symbol, []),
                    tables=tables,
                    start=start,
                    end=end,
                    source=self.settings.financial.source,
                )
                if not symbol_records:
                    empty += 1
                records.extend(symbol_records)
                completed.append(symbol)
            try:
                result = self.financial.upsert(records)
            except Exception as exc:
                for symbol in completed:
                    failed += 1
                    errors.append(
                        {
                            "symbol": symbol,
                            "stage": "sqlite",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    self.catalog.finish_job_item(item_ids[symbol], status="failed", error=exc)
            else:
                inserted += result.inserted_rows
                updated += result.updated_rows
                unchanged += result.unchanged_rows
                deleted += result.deleted_rows
                shard_paths.update(result.shards)
                success += len(completed)
                self.catalog.finish_job_items_completed(
                    [(item_ids[symbol], 0, 0) for symbol in completed]
                )
            print(
                json.dumps(
                    {
                        "event": "financial_backfill_progress",
                        "job_id": job_id,
                        "batch": batch_index,
                        "batches": len(batches),
                        "symbols": len(batch),
                        "success_total": success,
                        "failed_total": failed,
                        "inserted_total": inserted,
                        "updated_total": updated,
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )

        paths = [
            self.financial.shards.fundamentals_path(year)
            for year in range(start // 10_000, end // 10_000 + 1)
        ]
        coverage = self.financial.summary(paths)
        self._record_shards(paths)
        status = "completed" if failed == 0 else "degraded"
        elapsed = round(time.monotonic() - started, 3)
        report_dir = Path("reports/data/jobs")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_json = report_dir / f"{job_id}.json"
        report_text = report_dir / f"{job_id}.txt"
        payload: dict[str, object] = {
            "job_id": job_id,
            "status": status,
            "requested_start": start,
            "requested_end": end,
            "symbol_count": len(symbols),
            "tables": tables,
            "resume": resume,
            "unsupported_upstream_tables": ["PerShare"],
            "success_items": success,
            "failed_items": failed,
            "empty_symbols": empty,
            "inserted_rows": inserted,
            "updated_rows": updated,
            "unchanged_rows": unchanged,
            "deleted_rows": deleted,
            "shards": [str(path) for path in paths],
            "coverage": coverage,
            "integrity": {path.name: self._integrity(path) for path in paths if path.exists()},
            "errors": errors,
            "elapsed_seconds": elapsed,
        }
        report_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report_text.write_text(_human_summary(payload), encoding="utf-8")
        self.catalog.finish_job(
            job_id,
            status=status,
            total_items=success + failed,
            success_items=success,
            failed_items=failed,
            inserted_rows=inserted,
            updated_rows=updated,
            summary=payload,
        )
        return FinancialBackfillSummary(
            job_id=job_id,
            requested_start=start,
            requested_end=end,
            symbol_count=len(symbols),
            success_items=success,
            failed_items=failed,
            empty_symbols=empty,
            inserted_rows=inserted,
            updated_rows=updated,
            unchanged_rows=unchanged,
            deleted_rows=deleted,
            shards=tuple(str(path) for path in paths),
            coverage=coverage,
            elapsed_seconds=elapsed,
            report_json=str(report_json),
            report_text=str(report_text),
        )

    def _download_isolated(
        self, symbols: list[str], *, tables: list[str], start: int, end: int
    ) -> tuple[list[str], dict[str, BaseException]]:
        try:
            self.gateway.download_financial_data(symbols, tables, str(start), str(end))
            return symbols, {}
        except DataGatewayError:
            succeeded: list[str] = []
            errors: dict[str, BaseException] = {}
            for symbol in symbols:
                try:
                    self.gateway.download_financial_data([symbol], tables, str(start), str(end))
                    succeeded.append(symbol)
                except DataGatewayError as exc:
                    errors[symbol] = exc
            return succeeded, errors

    def _read_isolated(
        self, symbols: list[str], *, tables: list[str], start: int, end: int
    ) -> tuple[dict[str, list[dict[str, pd.DataFrame]]], dict[str, BaseException]]:
        if not symbols:
            return {}, {}
        try:
            # The requested scope is a report-period scope.  XtData's report_time query
            # returns every matching row together with m_anntime, which we retain for
            # point-in-time filtering.  Repeating the same payload with announce_time
            # doubles transport and normalization work without adding in-scope records.
            report_frames = self.gateway.get_financial_data(
                symbols,
                tables,
                str(start),
                str(end),
                report_type="report_time",
            )
            return {symbol: [report_frames.get(symbol, {})] for symbol in symbols}, {}
        except DataGatewayError:
            result: dict[str, list[dict[str, pd.DataFrame]]] = {}
            errors: dict[str, BaseException] = {}
            for symbol in symbols:
                try:
                    result[symbol] = [
                        self.gateway.get_financial_data(
                            [symbol],
                            tables,
                            str(start),
                            str(end),
                            report_type="report_time",
                        ).get(symbol, {})
                    ]
                except DataGatewayError as exc:
                    errors[symbol] = exc
            return result, errors

    def _record_shards(self, paths: list[Path]) -> None:
        for path in paths:
            if not path.exists():
                continue
            with connect_sqlite(path, readonly=True) as connection:
                row = connection.execute(
                    "SELECT min(NULLIF(report_date, 0)), max(NULLIF(report_date, 0)), "
                    "count(*) FROM financial_records"
                ).fetchone()
            self.catalog.record_shard(
                path,
                dataset="fundamentals",
                market=None,
                period="financial",
                start_date=None if row[0] is None else int(row[0]),
                end_date=None if row[1] is None else int(row[1]),
                row_count=int(row[2]),
                writable=True,
                checksum=None,
            )

    @staticmethod
    def _integrity(path: Path) -> str:
        with connect_sqlite(path, readonly=True) as connection:
            return integrity_check(connection)


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _clean_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_value(item) for item in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _first_date(row: dict[str, Any], names: tuple[str, ...]) -> int:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        text = str(value).replace("-", "").replace("/", "")
        digits = "".join(character for character in text if character.isdigit())
        if len(digits) >= 8:
            candidate = int(digits[:8])
            if 19900101 <= candidate <= 99991231:
                return candidate
    return 0


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


def _human_summary(payload: dict[str, object]) -> str:
    coverage = payload["coverage"]
    assert isinstance(coverage, dict)
    lines = [
        f"job_id: {payload['job_id']}",
        f"status: {payload['status']}",
        f"requested: {payload['requested_start']}..{payload['requested_end']}",
        f"symbols: {payload['symbol_count']}",
        f"items success/failed/empty: "
        f"{payload['success_items']}/{payload['failed_items']}/{payload['empty_symbols']}",
        f"rows inserted/updated/unchanged/deleted: "
        f"{payload['inserted_rows']}/{payload['updated_rows']}/"
        f"{payload['unchanged_rows']}/{payload['deleted_rows']}",
        f"stored total rows: {coverage.get('total_rows')}",
        f"rows by table: {json.dumps(coverage.get('rows_by_table'), ensure_ascii=False)}",
        f"elapsed_seconds: {payload['elapsed_seconds']}",
    ]
    return "\n".join(lines) + "\n"


def summary_dict(summary: FinancialBackfillSummary) -> dict[str, object]:
    return asdict(summary)
