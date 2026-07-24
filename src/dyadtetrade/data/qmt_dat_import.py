from __future__ import annotations

import hashlib
import json
import shutil
import struct
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dyadtetrade.data.config import DataPlatformSettings
from dyadtetrade.data.models import BarRecord
from dyadtetrade.data.quality import hard_failures, validate_bars
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository

CHINA_TZ = ZoneInfo("Asia/Shanghai")
QMT_DAT_HEADER = 0x7FFFFFFFFFFFFFFE
QMT_DAT_RECORD = struct.Struct("<16I")
QMT_DAT_TRAILERS = frozenset({0x7FF9, 0x7FFD})


class QmtDatFormatError(ValueError):
    """Raised when a local QMT DAT file is not the validated 64-byte bar format."""


@dataclass(frozen=True, slots=True)
class QmtDatImportSummary:
    job_id: str
    source_data_dir: str
    requested_start: int
    requested_end: int
    symbol_count: int
    available_files: int
    missing_files: int
    symbols_with_rows: int
    success_items: int
    failed_items: int
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int
    quality_issues: int
    first_trade_date: int | None
    last_trade_date: int | None
    shards: tuple[str, ...]
    elapsed_seconds: float
    report_json: str
    report_text: str


class QmtMinuteDatReader:
    """Read the fixed-record local minute format used by this installed QMT build."""

    def __init__(self, data_dir: Path, *, source: str = "qmt_big_local_dat_v1") -> None:
        self.data_dir = data_dir.resolve()
        self.source = source

    def path_for(self, symbol: str) -> Path:
        code, market = symbol.upper().rsplit(".", 1)
        return self.data_dir / market / "60" / f"{code}.DAT"

    def read(self, symbol: str, *, start: int, end: int) -> list[BarRecord]:
        path = self.path_for(symbol)
        if not path.exists():
            return []
        size = path.stat().st_size
        if size < 8 or (size - 8) % QMT_DAT_RECORD.size:
            raise QmtDatFormatError(f"invalid QMT DAT size: {path} ({size})")
        ingested_at = time.time_ns() // 1_000_000
        result: list[BarRecord] = []
        previous_seconds: int | None = None
        with path.open("rb") as handle:
            raw_header = handle.read(8)
            header = struct.unpack("<Q", raw_header)[0]
            if header != QMT_DAT_HEADER:
                raise QmtDatFormatError(f"unknown QMT DAT header: {path} ({header:#x})")
            while raw_record := handle.read(QMT_DAT_RECORD.size):
                values = QMT_DAT_RECORD.unpack(raw_record)
                seconds = int(values[0])
                if seconds % 60:
                    raise QmtDatFormatError(f"unaligned minute timestamp in {path}: {seconds}")
                if previous_seconds is not None and seconds <= previous_seconds:
                    raise QmtDatFormatError(f"non-monotonic QMT DAT timestamps: {path}")
                previous_seconds = seconds
                local = datetime.fromtimestamp(seconds, tz=CHINA_TZ)
                trade_date = int(local.strftime("%Y%m%d"))
                if trade_date > end:
                    break
                if trade_date < start:
                    continue
                if values[15] not in QMT_DAT_TRAILERS:
                    raise QmtDatFormatError(
                        f"unknown QMT DAT record trailer: {path} ({values[15]:#x})"
                    )
                prices = tuple(float(value) / 1000.0 for value in values[1:5])
                if min(prices) < 0 or prices[1] < max(prices) or prices[2] > min(prices):
                    raise QmtDatFormatError(f"invalid OHLC record in {path} at {seconds}")
                amount = float((int(values[9]) << 32) | int(values[8]))
                result.append(
                    BarRecord(
                        symbol=symbol.upper(),
                        ts=seconds * 1000,
                        trade_date=trade_date,
                        open=prices[0],
                        high=prices[1],
                        low=prices[2],
                        close=prices[3],
                        volume=int(values[6]),
                        amount=amount,
                        settlement_price=(None if values[5] == 0 else float(values[5]) / 1000.0),
                        open_interest=float(values[10]),
                        pre_close=(None if values[13] == 0 else float(values[13]) / 1000.0),
                        suspend_flag=int(values[14]),
                        source=self.source,
                        ingested_at=ingested_at,
                        source_hash=hashlib.blake2b(
                            raw_record, digest_size=8, person=b"qmt-1m-v1"
                        ).hexdigest(),
                    )
                )
        return result


class QmtDatImportRunner:
    def __init__(
        self,
        settings: DataPlatformSettings,
        data_dir: Path,
        *,
        source: str = "qmt_big_local_dat_v1",
    ) -> None:
        self.settings = settings
        self.reader = QmtMinuteDatReader(data_dir, source=source)
        self.catalog = CatalogRepository(settings.storage)
        self.bars = BarRepository(settings.storage)

    def run(
        self,
        *,
        start: int,
        end: int,
        markets: set[str] | None = None,
        instrument_types: tuple[str, ...] = ("stock", "etf"),
        symbols_override: list[str] | None = None,
    ) -> QmtDatImportSummary:
        started = time.monotonic()
        self._initialize()
        selected_markets = {item.upper() for item in (markets or {"SH", "SZ", "BJ"})}
        unsupported = selected_markets - {"SH", "SZ", "BJ"}
        if unsupported:
            raise ValueError("unsupported markets: " + ", ".join(sorted(unsupported)))
        supported_types = {"stock", "etf"}
        unknown_types = set(instrument_types) - supported_types
        if unknown_types:
            raise ValueError("unsupported instrument types: " + ", ".join(sorted(unknown_types)))
        symbols = [
            symbol
            for symbol in self.catalog.active_symbols(instrument_types)
            if symbol.rsplit(".", 1)[-1] in selected_markets
        ]
        if symbols_override:
            selected_symbols = {symbol.upper() for symbol in symbols_override}
            unknown_symbols = selected_symbols - set(symbols)
            if unknown_symbols:
                raise ValueError(
                    "requested symbols are outside the selected active catalog: "
                    + ", ".join(sorted(unknown_symbols))
                )
            symbols = [symbol for symbol in symbols if symbol in selected_symbols]
        open_days = set(self.catalog.open_days("SH", start, end))
        if not open_days:
            raise ValueError(f"no open trading days in requested interval: {start}..{end}")
        job_id = f"qmt-dat-import-{datetime.now(CHINA_TZ):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        self.catalog.create_job(
            job_id,
            "qmt_dat_import",
            requested_start=start,
            requested_end=end,
            effective_start=min(open_days),
            effective_end=max(open_days),
        )
        available = missing = with_rows = success = failed = 0
        inserted = updated = unchanged = issue_count = 0
        first_date: int | None = None
        last_date: int | None = None
        shards: set[str] = set()
        errors: list[dict[str, str]] = []
        batch_size = self.settings.backfill.read_batch_symbols
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset : offset + batch_size]
            batch_bars: list[BarRecord] = []
            item_ids: dict[str, int] = {}
            completed_without_rows: list[tuple[int, int, int]] = []
            for symbol in batch:
                path = self.reader.path_for(symbol)
                if not path.exists():
                    missing += 1
                    success += 1
                    continue
                available += 1
                item_id = self.catalog.start_job_item(
                    job_id,
                    dataset="bars",
                    period="1m",
                    symbol=symbol,
                    requested_start=start,
                    requested_end=end,
                    effective_start=min(open_days),
                    effective_end=max(open_days),
                )
                item_ids[symbol] = item_id
                try:
                    records = self.reader.read(symbol, start=start, end=end)
                except Exception as exc:
                    failed += 1
                    errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
                    self.catalog.finish_job_item(item_id, status="failed", error=exc)
                    continue
                issues = validate_bars(
                    records,
                    period="1m",
                    requested_end=end,
                    open_days=open_days,
                    job_id=job_id,
                )
                issue_count += self.catalog.record_issues(issues)
                failures = hard_failures(issues)
                if failures:
                    failed += 1
                    error = QmtDatFormatError("; ".join(sorted({issue.rule for issue in failures})))
                    errors.append({"symbol": symbol, "error": str(error)})
                    self.catalog.finish_job_item(item_id, status="failed", error=error)
                    continue
                success += 1
                if not records:
                    completed_without_rows.append((item_id, 0, 0))
                    continue
                with_rows += 1
                first = records[0].trade_date
                last = records[-1].trade_date
                first_date = first if first_date is None else min(first_date, first)
                last_date = last if last_date is None else max(last_date, last)
                batch_bars.extend(records)
            if completed_without_rows:
                self.catalog.finish_job_items_completed(completed_without_rows)
            if not batch_bars:
                self._print_progress(
                    job_id=job_id,
                    completed=min(offset + len(batch), len(symbols)),
                    total=len(symbols),
                    success=success,
                    failed=failed,
                    inserted=inserted,
                )
                continue
            result = self.bars.upsert("1m", batch_bars)
            inserted += result.inserted_rows
            updated += result.updated_rows
            unchanged += result.unchanged_rows
            shards.update(str(path) for path in result.shards)
            completed: list[tuple[int, int, int]] = []
            for symbol, counts in result.by_symbol.items():
                completed.append((item_ids[symbol], counts.inserted_rows, counts.updated_rows))
            self.catalog.finish_job_items_completed(completed)
            batch_stats = self.bars.symbol_stats_many("1m", list(result.by_symbol))
            self.catalog.update_watermarks(
                (
                    ("1m", symbol, first_ts, last_ts, row_count)
                    for symbol, (first_ts, last_ts, row_count) in batch_stats.items()
                ),
                dataset="bars",
                job_id=job_id,
            )
            self._print_progress(
                job_id=job_id,
                completed=min(offset + len(batch), len(symbols)),
                total=len(symbols),
                success=success,
                failed=failed,
                inserted=inserted,
            )
        stats = self.bars.symbol_stats_many("1m", symbols)
        self.catalog.update_watermarks(
            (
                ("1m", symbol, first_ts, last_ts, row_count)
                for symbol, (first_ts, last_ts, row_count) in stats.items()
            ),
            dataset="bars",
            job_id=job_id,
        )
        elapsed_seconds = round(time.monotonic() - started, 3)
        payload: dict[str, object] = {
            "job_id": job_id,
            "source_data_dir": str(self.reader.data_dir),
            "source": self.reader.source,
            "format": {
                "header": f"{QMT_DAT_HEADER:#x}",
                "record_bytes": QMT_DAT_RECORD.size,
                "trailers": [f"{item:#x}" for item in sorted(QMT_DAT_TRAILERS)],
            },
            "requested_start": start,
            "requested_end": end,
            "symbol_count": len(symbols),
            "available_files": available,
            "missing_files": missing,
            "symbols_with_rows": with_rows,
            "success_items": success,
            "failed_items": failed,
            "inserted_rows": inserted,
            "updated_rows": updated,
            "unchanged_rows": unchanged,
            "quality_issues": issue_count,
            "first_trade_date": first_date,
            "last_trade_date": last_date,
            "shards": sorted(shards),
            "errors": errors,
            "elapsed_seconds": elapsed_seconds,
        }
        status = "completed" if failed == 0 else "degraded"
        self.catalog.finish_job(
            job_id,
            status=status,
            total_items=len(symbols),
            success_items=success,
            failed_items=failed,
            inserted_rows=inserted,
            updated_rows=updated,
            summary=payload,
            error_text=None if not errors else json.dumps(errors, ensure_ascii=False),
        )
        report_json, report_text = self._write_reports(payload)
        return QmtDatImportSummary(
            job_id=job_id,
            source_data_dir=str(self.reader.data_dir),
            requested_start=start,
            requested_end=end,
            symbol_count=len(symbols),
            available_files=available,
            missing_files=missing,
            symbols_with_rows=with_rows,
            success_items=success,
            failed_items=failed,
            inserted_rows=inserted,
            updated_rows=updated,
            unchanged_rows=unchanged,
            quality_issues=issue_count,
            first_trade_date=first_date,
            last_trade_date=last_date,
            shards=tuple(sorted(shards)),
            elapsed_seconds=elapsed_seconds,
            report_json=str(report_json),
            report_text=str(report_text),
        )

    def _initialize(self) -> None:
        if not self.reader.data_dir.exists():
            raise FileNotFoundError(self.reader.data_dir)
        free = shutil.disk_usage(self.settings.storage.root.resolve().anchor).free
        required = int(self.settings.storage.minimum_free_gib * 1024**3)
        if free < required:
            raise OSError(f"insufficient free space for QMT DAT import: {free / 1024**3:.2f} GiB")
        self.catalog.initialize()
        self.bars.initialize()

    @staticmethod
    def _print_progress(
        *,
        job_id: str,
        completed: int,
        total: int,
        success: int,
        failed: int,
        inserted: int,
    ) -> None:
        print(
            json.dumps(
                {
                    "event": "qmt_dat_import_progress",
                    "job_id": job_id,
                    "completed": completed,
                    "total": total,
                    "success": success,
                    "failed": failed,
                    "inserted": inserted,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    @staticmethod
    def _write_reports(payload: dict[str, object]) -> tuple[Path, Path]:
        report_root = Path("reports/data/jobs")
        report_root.mkdir(parents=True, exist_ok=True)
        job_id = str(payload["job_id"])
        json_path = report_root / f"{job_id}.json"
        text_path = report_root / f"{job_id}.txt"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        text_path.write_text(
            "\n".join(
                [
                    f"job_id: {job_id}",
                    f"source_data_dir: {payload['source_data_dir']}",
                    f"requested: {payload['requested_start']}..{payload['requested_end']}",
                    "symbols/files/missing: "
                    f"{payload['symbol_count']}/{payload['available_files']}/"
                    f"{payload['missing_files']}",
                    f"symbols_with_rows: {payload['symbols_with_rows']}",
                    f"items success/failed: {payload['success_items']}/{payload['failed_items']}",
                    "rows inserted/updated/unchanged: "
                    f"{payload['inserted_rows']}/{payload['updated_rows']}/"
                    f"{payload['unchanged_rows']}",
                    f"actual dates: {payload['first_trade_date']}..{payload['last_trade_date']}",
                    f"quality issues: {payload['quality_issues']}",
                    f"elapsed_seconds: {payload['elapsed_seconds']}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return json_path, text_path


def summary_dict(summary: QmtDatImportSummary) -> dict[str, object]:
    return asdict(summary)
