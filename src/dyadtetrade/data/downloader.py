from __future__ import annotations

import json
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dyadtetrade.data.config import DataPlatformSettings
from dyadtetrade.data.gateway import DataGatewayError, MarketDataGateway
from dyadtetrade.data.models import BarRecord, QualityIssueRecord
from dyadtetrade.data.normalizer import normalize_xtdata_frame
from dyadtetrade.data.quality import hard_failures, validate_bars
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository
from dyadtetrade.data.universe import (
    UniverseSnapshot,
    actions_from_xtdata,
    discover_universe,
    instrument_from_xtdata,
    is_unlaunched_placeholder,
    sector_instrument_type,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")
MINUTE_AVAILABLE_FROM = {"SH": 20250711, "SZ": 20250711, "BJ": 20250716}
RAW_FIELDS = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "settle",
    "openInterest",
]


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    job_id: str
    requested_start: int
    requested_end: int
    effective_start: int
    effective_end: int
    symbol_count: int
    current_symbol_count: int
    delisted_or_historical_count: int
    success_items: int
    failed_items: int
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int
    quality_issues: int
    shards: tuple[str, ...]
    elapsed_seconds: float
    report_json: str
    report_text: str


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    success: int
    failed: int
    inserted: int
    updated: int
    unchanged: int
    quality_issues: int
    shards: tuple[str, ...]
    errors: tuple[dict[str, str], ...]


class BackfillRunner:
    def __init__(self, settings: DataPlatformSettings, gateway: MarketDataGateway) -> None:
        self.settings = settings
        self.gateway = gateway
        self.catalog = CatalogRepository(settings.storage)
        self.bars = BarRepository(settings.storage)
        self._upstream_data_dir: Path | None = settings.qmt.data_dir

    def initialize(self) -> None:
        self._ensure_disk_space()
        if self._upstream_data_dir is None:
            reported = self.gateway.doctor().get("data_dir")
            if reported:
                self._upstream_data_dir = Path(str(reported))
        self._ensure_upstream_disk_space()
        self.catalog.initialize()
        self.bars.initialize()

    def run(
        self,
        *,
        start: int,
        end: int,
        periods: list[str],
        sectors: list[str],
        include_delisted: bool,
        job_type: str = "backfill",
        symbols_override: list[str] | None = None,
    ) -> BackfillSummary:
        started = time.monotonic()
        self.initialize()
        calendars = self._refresh_calendars(start, end)
        effective_start, effective_end = self.catalog.clip_to_open_days("SH", start, end)
        self.gateway.refresh_sector_data()
        if include_delisted:
            self.gateway.refresh_history_contracts()
        universe = discover_universe(
            self.gateway,
            sectors=sectors,
            extra_sectors=self.settings.universe.extra_sectors,
            include_delisted=include_delisted,
            existing_symbols=(
                self.catalog.all_instrument_symbols(
                    tuple(
                        sorted(
                            {
                                sector_instrument_type(sector)
                                for sector in [*sectors, *self.settings.universe.extra_sectors]
                            }
                        )
                    )
                )
                if include_delisted
                else None
            ),
        )
        if symbols_override:
            selected = {symbol.upper() for symbol in symbols_override}
            unknown = selected - set(universe.symbols)
            if unknown:
                raise ValueError(
                    "requested symbols are outside the discovered A-share universe: "
                    + ", ".join(sorted(unknown))
                )
            universe = UniverseSnapshot(
                symbols=tuple(sorted(selected)),
                current_symbols=frozenset(selected & universe.current_symbols),
                sectors={
                    sector: tuple(symbol for symbol in symbols if symbol in selected)
                    for sector, symbols in universe.sectors.items()
                    if any(symbol in selected for symbol in symbols)
                },
                instrument_types={
                    symbol: universe.instrument_types.get(symbol, "stock") for symbol in selected
                },
                expired_sectors=universe.expired_sectors,
            )
        job_id = f"{job_type}-{datetime.now(CHINA_TZ):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        self.catalog.create_job(
            job_id,
            job_type,
            requested_start=start,
            requested_end=end,
            effective_start=effective_start,
            effective_end=effective_end,
        )
        self._record_universe(universe, effective_end)
        self._refresh_instruments_and_actions(
            universe.symbols,
            universe.current_symbols,
            universe.instrument_types,
            effective_start,
            effective_end,
        )
        lifecycles = self.catalog.instrument_lifecycles(universe.symbols)
        open_days = set(calendars["SH"])
        success = failed = inserted = updated = unchanged = issue_count = 0
        shard_paths: set[str] = set()
        errors: list[dict[str, str]] = []
        ordered_periods = [period for period in ("1d", "1m") if period in periods]
        for period in ordered_periods:
            previously_completed = self.catalog.completed_symbols_for_scope(
                dataset="bars", period=period, start=start, end=end
            )
            windows = self._period_windows(period, effective_start, effective_end, calendars["SH"])
            for window_start, window_end in windows:
                stored_stats = self.bars.date_stats_many(
                    period, list(universe.symbols), window_start, window_end
                )
                resume_groups: dict[tuple[int, int], list[str]] = {}
                for symbol in universe.symbols:
                    resume_window = self._resume_window(
                        symbol,
                        period=period,
                        start=window_start,
                        end=window_end,
                        trading_days=calendars["SH"],
                        stored_stats=stored_stats[symbol],
                        lifecycle=lifecycles[symbol],
                        scope_completed=symbol in previously_completed,
                    )
                    if resume_window is not None:
                        resume_groups.setdefault(resume_window, []).append(symbol)
                for (item_start, item_end), group_symbols in sorted(resume_groups.items()):
                    batches = _batches(group_symbols, self.settings.backfill.download_batch_symbols)
                    for batch_index, symbols in enumerate(batches, start=1):
                        outcome = self._process_batch(
                            symbols,
                            job_id=job_id,
                            period=period,
                            requested_start=start,
                            requested_end=end,
                            effective_start=item_start,
                            effective_end=item_end,
                            quality_end=effective_end,
                            open_days=open_days,
                        )
                        success += outcome.success
                        failed += outcome.failed
                        inserted += outcome.inserted
                        updated += outcome.updated
                        unchanged += outcome.unchanged
                        issue_count += outcome.quality_issues
                        shard_paths.update(outcome.shards)
                        errors.extend(outcome.errors)
                        print(
                            json.dumps(
                                {
                                    "event": "backfill_progress",
                                    "job_id": job_id,
                                    "period": period,
                                    "window": [item_start, item_end],
                                    "batch": batch_index,
                                    "batches": len(batches),
                                    "symbols": len(symbols),
                                    "success_total": success,
                                    "failed_total": failed,
                                    "inserted_total": inserted,
                                },
                                ensure_ascii=False,
                            ),
                            file=sys.stderr,
                            flush=True,
                        )
        status = "completed" if failed == 0 else "degraded"
        report_dir = Path("reports/data/jobs")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_json = report_dir / f"{job_id}.json"
        report_text = report_dir / f"{job_id}.txt"
        elapsed_seconds = round(time.monotonic() - started, 3)
        payload = {
            "job_id": job_id,
            "requested_start": start,
            "requested_end": end,
            "effective_start": effective_start,
            "effective_end": effective_end,
            "symbol_count": len(universe.symbols),
            "current_symbol_count": len(universe.current_symbols),
            "delisted_or_historical_count": len(universe.symbols) - len(universe.current_symbols),
            "success_items": success,
            "failed_items": failed,
            "inserted_rows": inserted,
            "updated_rows": updated,
            "unchanged_rows": unchanged,
            "quality_issues": issue_count,
            "shards": sorted(shard_paths),
            "errors": errors,
            "database_sizes": self._database_sizes(),
            "elapsed_seconds": elapsed_seconds,
            "status": status,
        }
        report_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
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
        return BackfillSummary(
            job_id=job_id,
            requested_start=start,
            requested_end=end,
            effective_start=effective_start,
            effective_end=effective_end,
            symbol_count=len(universe.symbols),
            current_symbol_count=len(universe.current_symbols),
            delisted_or_historical_count=len(universe.symbols) - len(universe.current_symbols),
            success_items=success,
            failed_items=failed,
            inserted_rows=inserted,
            updated_rows=updated,
            unchanged_rows=unchanged,
            quality_issues=issue_count,
            shards=tuple(sorted(shard_paths)),
            elapsed_seconds=elapsed_seconds,
            report_json=str(report_json),
            report_text=str(report_text),
        )

    def _refresh_calendars(self, start: int, end: int) -> dict[str, list[int]]:
        self.gateway.download_holiday_data()
        result: dict[str, list[int]] = {}
        for market in ("SH", "SZ", "BJ"):
            values = self.gateway.get_trading_calendar(market, str(start), str(end))
            days = sorted({int(value[:8]) for value in values if value[:8].isdigit()})
            if not days and market != "SH":
                days = list(result["SH"])
                source = "xtdata:SH-calendar-fallback"
            else:
                source = "xtdata"
            self.catalog.upsert_calendar(market, days, source=source)
            result[market] = days
        return result

    def _record_universe(self, universe: UniverseSnapshot, snapshot_date: int) -> None:
        today = int(datetime.now(CHINA_TZ).strftime("%Y%m%d"))
        current_open_days = self.catalog.open_days("SH", 19900101, today)
        if not current_open_days or snapshot_date != current_open_days[-1]:
            return
        for sector, symbols in universe.sectors.items():
            self.catalog.replace_sector_snapshots(
                snapshot_date,
                [(str(sector), list(symbols), len(symbols))],
                source="xtdata_sector_current_v1",
            )

    def _refresh_instruments_and_actions(
        self,
        symbols: tuple[str, ...],
        current_symbols: frozenset[str],
        instrument_types: dict[str, str],
        start: int,
        end: int,
    ) -> None:
        for batch in _batches(list(symbols), self.settings.backfill.download_batch_symbols):
            try:
                details = self.gateway.get_instrument_details(batch)
                action_frames = self.gateway.get_divid_factors_batch(batch, "19900101", str(end))
                self.catalog.upsert_instruments(
                    [
                        instrument_from_xtdata(
                            symbol,
                            details.get(symbol, {}),
                            is_current=symbol in current_symbols,
                            instrument_type=instrument_types.get(symbol, "stock"),
                        )
                        for symbol in batch
                    ]
                )
                batch_actions = []
                for symbol in batch:
                    frame = action_frames.get(symbol)
                    if frame is not None:
                        batch_actions.extend(actions_from_xtdata(symbol, frame))
                self.catalog.upsert_actions(batch_actions)
            except DataGatewayError:
                for symbol in batch:
                    try:
                        detail = self.gateway.get_instrument_detail(symbol)
                        self.catalog.upsert_instruments(
                            [
                                instrument_from_xtdata(
                                    symbol,
                                    detail,
                                    is_current=symbol in current_symbols,
                                    instrument_type=instrument_types.get(symbol, "stock"),
                                )
                            ]
                        )
                        actions = actions_from_xtdata(
                            symbol,
                            self.gateway.get_divid_factors(symbol, "19900101", str(end)),
                        )
                        self.catalog.upsert_actions(actions)
                    except DataGatewayError:
                        continue

    def _resume_window(
        self,
        symbol: str,
        *,
        period: str,
        start: int,
        end: int,
        trading_days: list[int],
        stored_stats: tuple[int | None, int | None, int] | None = None,
        lifecycle: tuple[int | None, int | None] | None = None,
        scope_completed: bool = False,
    ) -> tuple[int, int] | None:
        list_date, delist_date = lifecycle or self.catalog.instrument_lifecycle(symbol)
        lifecycle_start = max(start, list_date) if list_date else start
        lifecycle_end = min(end, delist_date) if delist_date else end
        days = [day for day in trading_days if lifecycle_start <= day <= lifecycle_end]
        if not days:
            return None
        first_stored, last_stored, row_count = stored_stats or self.bars.date_stats(
            period, symbol, days[0], days[-1]
        )
        if scope_completed and (row_count == 0 or last_stored is None):
            # A successful empty scope is a durable result (for example a future ETF or
            # a delisted code absent from the broker history server), not a reason to
            # hammer the same upstream endpoint on every resume.
            return None
        if scope_completed and first_stored is not None and last_stored is not None:
            overlap = self.settings.backfill.overlap_trading_days
            if overlap == 0:
                return None
            last_index = max(index for index, day in enumerate(days) if day <= last_stored)
            return days[max(0, last_index - overlap + 1)], days[-1]
        if row_count == 0 or first_stored is None or last_stored is None:
            # QMT naturally returns no rows before listing or after delisting.  Keeping
            # the common requested window lets thousands of different IPO dates share
            # large vendor batches instead of degenerating into one request per date.
            return start, end
        if first_stored > days[0]:
            # A later watermark must not hide an unfinished historical prefix.
            return start, end
        overlap = self.settings.backfill.overlap_trading_days
        if overlap == 0:
            remaining = [day for day in days if day > last_stored]
            return None if not remaining else (remaining[0], days[-1])
        last_index = max(index for index, day in enumerate(days) if day <= last_stored)
        resume_index = max(0, last_index - overlap + 1)
        return days[resume_index], days[-1]

    def _process_batch(
        self,
        symbols: list[str],
        *,
        job_id: str,
        period: str,
        requested_start: int,
        requested_end: int,
        effective_start: int,
        effective_end: int,
        quality_end: int,
        open_days: set[int],
    ) -> BatchOutcome:
        self._ensure_disk_space()
        self._ensure_upstream_disk_space()
        item_ids = {
            symbol: self.catalog.start_job_item(
                job_id,
                dataset="bars",
                period=period,
                symbol=symbol,
                requested_start=requested_start,
                requested_end=requested_end,
                effective_start=effective_start,
                effective_end=effective_end,
            )
            for symbol in symbols
        }
        if period == "1m" and all(
            effective_end < MINUTE_AVAILABLE_FROM.get(symbol.rsplit(".", 1)[-1], 20250711)
            for symbol in symbols
        ):
            issues = [
                QualityIssueRecord(
                    severity="warning",
                    symbol=symbol,
                    period=period,
                    trade_date=effective_start,
                    rule="upstream_minute_unavailable_before_capability_floor",
                    details={
                        "effective_start": effective_start,
                        "effective_end": effective_end,
                        "available_from": MINUTE_AVAILABLE_FROM.get(
                            symbol.rsplit(".", 1)[-1], 20250711
                        ),
                        "fill_data": False,
                    },
                    job_id=job_id,
                )
                for symbol in symbols
            ]
            issue_count = self.catalog.record_issues(issues)
            self.catalog.finish_job_items_completed(
                [(item_id, 0, 0) for item_id in item_ids.values()]
            )
            return BatchOutcome(
                success=len(symbols),
                failed=0,
                inserted=0,
                updated=0,
                unchanged=0,
                quality_issues=issue_count,
                shards=(),
                errors=(),
            )
        downloaded, download_errors = self._download_isolated(
            symbols, period=period, start=effective_start, end=effective_end
        )
        failed = len(download_errors)
        success = inserted = updated = unchanged = issue_count = 0
        shard_paths: set[str] = set()
        errors = list(download_errors)
        for download_error in download_errors:
            symbol = download_error["symbol"]
            self.catalog.finish_job_item(
                item_ids[symbol],
                status="failed",
                error=RuntimeError(download_error["error"]),
            )
        for read_symbols in _batches(downloaded, self.settings.backfill.read_batch_symbols):
            try:
                frames = self.gateway.get_local_data(
                    read_symbols,
                    fields=RAW_FIELDS,
                    period=period,
                    start=str(effective_start),
                    end=str(effective_end),
                )
            except DataGatewayError as exc:
                for symbol in read_symbols:
                    failed += 1
                    self.catalog.finish_job_item(item_ids[symbol], status="failed", error=exc)
                    errors.append({"symbol": symbol, "period": period, "error": str(exc)})
                continue
            missing_symbols = [
                symbol
                for symbol in read_symbols
                if (frames.get(symbol) is None or frames[symbol].empty)
            ]
            if missing_symbols:
                for symbol in missing_symbols:
                    try:
                        self.gateway.download_history(
                            [symbol],
                            period=period,
                            start=str(effective_start),
                            end=str(effective_end),
                        )
                    except DataGatewayError:
                        continue
                try:
                    retried = self.gateway.get_local_data(
                        missing_symbols,
                        fields=RAW_FIELDS,
                        period=period,
                        start=str(effective_start),
                        end=str(effective_end),
                    )
                    frames.update(retried)
                except DataGatewayError:
                    pass
            valid_records: dict[str, list[BarRecord]] = {}
            batch_issues: list[QualityIssueRecord] = []
            for symbol in read_symbols:
                frame = frames.get(symbol)
                if frame is None or frame.empty:
                    if period == "1d" and self._is_unlaunched_placeholder(symbol):
                        issue_count += self.catalog.record_issues(
                            [
                                QualityIssueRecord(
                                    severity="warning",
                                    symbol=symbol,
                                    period=period,
                                    trade_date=effective_start,
                                    rule="qmt_etf_placeholder_without_listed_history",
                                    details={
                                        "effective_start": effective_start,
                                        "effective_end": effective_end,
                                        "fill_data": False,
                                    },
                                    job_id=job_id,
                                )
                            ]
                        )
                        self.catalog.finish_job_item(item_ids[symbol], status="completed")
                        success += 1
                        continue
                    if period == "1d" and self._is_official_delisted_symbol(symbol):
                        instrument = self.catalog.instrument(symbol) or {}
                        self.catalog.resolve_open_issues(
                            symbol,
                            period,
                            rules=("qmt_etf_placeholder_without_listed_history",),
                            except_job_id=job_id,
                        )
                        issue_count += self.catalog.record_issues(
                            [
                                QualityIssueRecord(
                                    severity="warning",
                                    symbol=symbol,
                                    period=period,
                                    trade_date=effective_start,
                                    rule="upstream_xtdata_delisted_history_unavailable",
                                    details={
                                        "effective_start": effective_start,
                                        "effective_end": effective_end,
                                        "list_date": instrument.get("list_date"),
                                        "delist_date": instrument.get("delist_date"),
                                        "catalog_source": instrument.get("source"),
                                        "download_succeeded": True,
                                        "local_rows_after_retry": 0,
                                        "instrument_detail_available": False,
                                    },
                                    job_id=job_id,
                                )
                            ]
                        )
                        self.catalog.finish_job_item(item_ids[symbol], status="completed")
                        success += 1
                        continue
                    if period == "1d" and self._is_index_symbol(symbol):
                        instrument = self.catalog.instrument(symbol) or {}
                        self.catalog.resolve_open_issues(
                            symbol,
                            period,
                            rules=("qmt_etf_placeholder_without_listed_history",),
                            except_job_id=job_id,
                        )
                        issue_count += self.catalog.record_issues(
                            [
                                QualityIssueRecord(
                                    severity="warning",
                                    symbol=symbol,
                                    period=period,
                                    trade_date=effective_start,
                                    rule="upstream_xtdata_index_history_unavailable",
                                    details={
                                        "effective_start": effective_start,
                                        "effective_end": effective_end,
                                        "instrument_name": instrument.get("name"),
                                        "catalog_source": instrument.get("source"),
                                        "download_succeeded": True,
                                        "local_rows_after_retry": 0,
                                    },
                                    job_id=job_id,
                                )
                            ]
                        )
                        self.catalog.finish_job_item(item_ids[symbol], status="completed")
                        success += 1
                        continue
                    market = symbol.rsplit(".", 1)[-1]
                    minute_floor = MINUTE_AVAILABLE_FROM.get(market, 20250711)
                    if period == "1m" and effective_end < minute_floor:
                        issue_count += self.catalog.record_issues(
                            [
                                QualityIssueRecord(
                                    severity="warning",
                                    symbol=symbol,
                                    period=period,
                                    trade_date=effective_start,
                                    rule="upstream_minute_unavailable_before_capability_floor",
                                    details={
                                        "effective_start": effective_start,
                                        "effective_end": effective_end,
                                        "available_from": minute_floor,
                                        "fill_data": False,
                                    },
                                    job_id=job_id,
                                )
                            ]
                        )
                        self.catalog.finish_job_item(item_ids[symbol], status="completed")
                        success += 1
                        continue
                    if (
                        period == "1m"
                        and self.bars.date_stats("1d", symbol, effective_start, effective_end)[2]
                        == 0
                    ):
                        issue_count += self.catalog.record_issues(
                            [
                                QualityIssueRecord(
                                    severity="warning",
                                    symbol=symbol,
                                    period=period,
                                    trade_date=effective_start,
                                    rule="empty_minute_window_without_daily_bar",
                                    details={
                                        "effective_start": effective_start,
                                        "effective_end": effective_end,
                                        "fill_data": False,
                                    },
                                    job_id=job_id,
                                )
                            ]
                        )
                        self.catalog.finish_job_item(item_ids[symbol], status="completed")
                        success += 1
                        continue
                    failed += 1
                    missing_error = RuntimeError("no local rows after download")
                    self.catalog.finish_job_item(
                        item_ids[symbol], status="failed", error=missing_error
                    )
                    errors.append(
                        {
                            "symbol": symbol,
                            "period": period,
                            "error": "no local rows after download",
                        }
                    )
                    continue
                try:
                    records = normalize_xtdata_frame(
                        frame, symbol=symbol, period=period, source="xtdata"
                    )
                    records, sanitization_issues = sanitize_xtdata_flow_fields(
                        records, period=period, job_id=job_id
                    )
                    records, calendar_issues = sanitize_xtdata_calendar_rows(
                        records,
                        period=period,
                        open_days=open_days,
                        job_id=job_id,
                    )
                    issues = validate_bars(
                        records,
                        period=period,
                        requested_end=quality_end,
                        open_days=open_days,
                        job_id=job_id,
                    )
                    issues = [*sanitization_issues, *calendar_issues, *issues]
                    batch_issues.extend(issues)
                    if hard_failures(issues):
                        raise ValueError(f"{len(hard_failures(issues))} hard quality failures")
                    valid_records[symbol] = records
                except Exception as exc:
                    failed += 1
                    self.catalog.finish_job_item(item_ids[symbol], status="failed", error=exc)
                    errors.append(
                        {
                            "symbol": symbol,
                            "period": period,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            issue_count += self.catalog.record_issues(batch_issues)
            if not valid_records:
                continue
            try:
                result = self.bars.upsert(
                    period,
                    [record for records in valid_records.values() for record in records],
                )
                stats = self.bars.symbol_stats_many(period, list(valid_records))
                self.catalog.update_watermarks(
                    [
                        (period, symbol, first_ts, last_ts, row_count)
                        for symbol, (first_ts, last_ts, row_count) in stats.items()
                    ],
                    dataset="bars",
                    job_id=job_id,
                )
                self.catalog.finish_job_items_completed(
                    [
                        (
                            item_ids[symbol],
                            result.by_symbol[symbol].inserted_rows,
                            result.by_symbol[symbol].updated_rows,
                        )
                        for symbol in valid_records
                    ]
                )
                for symbol in valid_records:
                    self.catalog.resolve_open_errors(symbol, period, except_job_id=job_id)
                success += len(valid_records)
                inserted += result.inserted_rows
                updated += result.updated_rows
                unchanged += result.unchanged_rows
                shard_paths.update(str(path) for path in result.shards)
            except Exception as exc:
                for symbol in valid_records:
                    failed += 1
                    self.catalog.finish_job_item(item_ids[symbol], status="failed", error=exc)
                    errors.append(
                        {
                            "symbol": symbol,
                            "period": period,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        return BatchOutcome(
            success=success,
            failed=failed,
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            quality_issues=issue_count,
            shards=tuple(sorted(shard_paths)),
            errors=tuple(errors),
        )

    def _is_unlaunched_placeholder(self, symbol: str) -> bool:
        instrument = self.catalog.instrument(symbol)
        if (
            not instrument
            or instrument.get("instrument_type") != "etf"
            or not instrument.get("raw_json")
        ):
            return False
        try:
            detail = json.loads(str(instrument["raw_json"]))
        except (TypeError, ValueError):
            return False
        return is_unlaunched_placeholder(detail)

    def _is_official_delisted_symbol(self, symbol: str) -> bool:
        instrument = self.catalog.instrument(symbol)
        if not instrument:
            return False
        return (
            instrument.get("instrument_type") == "stock"
            and not bool(instrument.get("is_active"))
            and str(instrument.get("source") or "").endswith("_official_delisted_v1")
        )

    def _is_index_symbol(self, symbol: str) -> bool:
        instrument = self.catalog.instrument(symbol)
        return bool(instrument and instrument.get("instrument_type") == "index")

    def _download_isolated(
        self, symbols: list[str], *, period: str, start: int, end: int
    ) -> tuple[list[str], list[dict[str, str]]]:
        try:
            self.gateway.download_history(symbols, period=period, start=str(start), end=str(end))
            return symbols, []
        except DataGatewayError:
            succeeded: list[str] = []
            errors: list[dict[str, str]] = []
            for symbol in symbols:
                try:
                    self.gateway.download_history(
                        [symbol], period=period, start=str(start), end=str(end)
                    )
                    succeeded.append(symbol)
                except DataGatewayError as exc:
                    errors.append({"symbol": symbol, "period": period, "error": str(exc)})
            return succeeded, errors

    @staticmethod
    def _period_windows(
        period: str, start: int, end: int, trading_days: list[int]
    ) -> list[tuple[int, int]]:
        if period == "1d":
            return [(start, end)]
        by_month: dict[str, list[int]] = {}
        for day in trading_days:
            if start <= day <= end:
                by_month.setdefault(str(day)[:6], []).append(day)
        return [(days[0], days[-1]) for _, days in sorted(by_month.items())]

    def _ensure_disk_space(self) -> None:
        root = self.settings.storage.root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free / (1024**3)
        if free < self.settings.storage.minimum_free_gib:
            raise RuntimeError(
                f"insufficient disk space: {free:.2f} GiB free, "
                f"requires {self.settings.storage.minimum_free_gib:.2f} GiB"
            )

    def _ensure_upstream_disk_space(self) -> None:
        if self._upstream_data_dir is None or not self._upstream_data_dir.exists():
            return
        free = shutil.disk_usage(self._upstream_data_dir).free / (1024**3)
        required = self.settings.qmt.minimum_cache_free_gib
        if free < required:
            raise RuntimeError(
                f"insufficient QMT cache disk space: {free:.2f} GiB free at "
                f"{self._upstream_data_dir}, requires {required:.2f} GiB"
            )

    def _database_sizes(self) -> dict[str, int]:
        return {
            path.name: path.stat().st_size
            for path in sorted(self.settings.storage.root.glob("*.db"))
        }


def sanitize_xtdata_flow_fields(
    records: list[BarRecord], *, period: str, job_id: str | None
) -> tuple[list[BarRecord], list[QualityIssueRecord]]:
    """Quarantine unrecoverable signed-overflow flow fields without losing prices."""
    cleaned: list[BarRecord] = []
    issues: list[QualityIssueRecord] = []
    for record in records:
        volume = record.volume
        amount = record.amount
        changed = False
        for field in ("volume", "amount"):
            value = getattr(record, field)
            if value is None or value >= 0:
                continue
            if field == "volume":
                volume = None
            else:
                amount = None
            changed = True
            issues.append(
                QualityIssueRecord(
                    severity="warning",
                    symbol=record.symbol,
                    period=period,
                    trade_date=record.trade_date,
                    rule=f"negative_{field}_quarantined",
                    details={
                        "raw_value": value,
                        "stored_value": None,
                        "reason": "vendor signed-overflow value cannot be reconstructed",
                    },
                    job_id=job_id,
                )
            )
        cleaned.append(replace(record, volume=volume, amount=amount) if changed else record)
    return cleaned, issues


def sanitize_xtdata_calendar_rows(
    records: list[BarRecord],
    *,
    period: str,
    open_days: set[int],
    job_id: str | None,
) -> tuple[list[BarRecord], list[QualityIssueRecord]]:
    """Quarantine QMT rows stamped on a closed session without losing valid history."""
    cleaned: list[BarRecord] = []
    issues: list[QualityIssueRecord] = []
    for record in records:
        if record.trade_date in open_days:
            cleaned.append(record)
            continue
        issues.append(
            QualityIssueRecord(
                severity="warning",
                symbol=record.symbol,
                period=period,
                trade_date=record.trade_date,
                rule="closed_session_row_quarantined",
                details={
                    "raw_bar": {
                        "ts": record.ts,
                        "open": record.open,
                        "high": record.high,
                        "low": record.low,
                        "close": record.close,
                        "volume": record.volume,
                        "amount": record.amount,
                    },
                    "stored": False,
                    "reason": "vendor row date is absent from the QMT trading calendar",
                },
                job_id=job_id,
            )
        )
    return cleaned, issues


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


def _human_summary(payload: dict[str, object]) -> str:
    lines = [
        f"job_id: {payload['job_id']}",
        f"status: {payload['status']}",
        f"requested: {payload['requested_start']}..{payload['requested_end']}",
        f"effective: {payload['effective_start']}..{payload['effective_end']}",
        f"symbols: {payload['symbol_count']}",
        f"items success/failed: {payload['success_items']}/{payload['failed_items']}",
        f"rows inserted/updated/unchanged: "
        f"{payload['inserted_rows']}/{payload['updated_rows']}/{payload['unchanged_rows']}",
        f"quality issues: {payload['quality_issues']}",
        f"elapsed_seconds: {payload['elapsed_seconds']}",
    ]
    return "\n".join(lines) + "\n"


def summary_dict(summary: BackfillSummary) -> dict[str, object]:
    return asdict(summary)
