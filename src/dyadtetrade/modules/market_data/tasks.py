from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.core.config import get_settings
from dyadtetrade.db.duckdb_store import refresh_market_views
from dyadtetrade.db.models import DownloadTask, DownloadTaskItem, Instrument
from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.db.repositories import (
    create_or_get_download_task,
    get_download_task,
    list_task_items,
    persist_quality_findings,
    upsert_data_file,
)
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.market_data.failures import (
    DownloadFailureAssessment,
    InstrumentFacts,
    classify_download_failure,
)
from dyadtetrade.modules.market_data.normalizer import OHLCV_FIELDS, TICK_FIELDS
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline
from dyadtetrade.modules.market_data.quality import QualityFinding, write_quality_report


def download_idempotency_key(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    period: str,
    adjust: str,
) -> str:
    payload = json.dumps(
        {
            "source": "xtquant",
            "symbols": sorted(symbols),
            "start_date": start_date,
            "end_date": end_date,
            "period": period,
            "adjust": adjust,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


class DownloadTaskRunner:
    def __init__(
        self,
        session: AsyncSession,
        *,
        gateway: XtQuantGateway | None = None,
        store: ParquetStore | None = None,
        report_root: Path | None = None,
        refresh_views: bool = True,
    ) -> None:
        self.session = session
        self.gateway = gateway or XtQuantGateway()
        self.store = store or ParquetStore()
        self.pipeline = MarketDataPipeline(self.store)
        self.settings = get_settings()
        self.report_root = report_root or Path("reports") / "data_quality"
        self.refresh_views = refresh_views

    async def create(
        self,
        *,
        symbols: list[str],
        start_date: str,
        end_date: str,
        period: str,
        adjust: str,
        max_retries: int | None = None,
    ) -> tuple[DownloadTask, bool]:
        key = download_idempotency_key(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust=adjust,
        )
        return await create_or_get_download_task(
            self.session,
            idempotency_key=key,
            symbols=symbols,
            period=period,
            adjust=adjust,
            start_date=start_date,
            end_date=end_date,
            max_retries=max_retries
            if max_retries is not None
            else self.settings.market_download_max_retries,
        )

    async def run(self, task_id: int) -> DownloadTask:
        task = await get_download_task(self.session, task_id)
        if task is None:
            raise ValueError(f"下载任务不存在：{task_id}")
        if task.status == "succeeded":
            return task

        task.status = "running"
        task.started_at = task.started_at or datetime.now()
        task.finished_at = None
        await self.session.commit()
        findings: list[QualityFinding] = []
        handled_symbols: set[str] = set()
        expected_days = await self._expected_days(task.start_date, task.end_date)

        for item in await list_task_items(self.session, task.id):
            if item.status == "succeeded":
                continue
            handled_symbols.add(item.symbol)
            if item.status == "failed" and (not item.retryable or item.quarantined_at):
                findings.append(self._stored_failure_finding(item))
                continue
            item_findings = await self._run_item(task, item, expected_days)
            findings.extend(item_findings)
            await self._update_progress(task)

        items = await list_task_items(self.session, task.id)
        succeeded = sum(item.status == "succeeded" for item in items)
        failed = sum(item.status == "failed" for item in items)
        task.succeeded_items = succeeded
        task.failed_items = failed
        task.completed_items = succeeded + failed
        task.progress = 100.0 if not items else round(task.completed_items / len(items) * 100.0, 2)
        task.status = "succeeded" if failed == 0 else ("failed" if succeeded == 0 else "partial")
        task.finished_at = datetime.now()

        report = write_quality_report(
            findings,
            report_root=self.report_root,
            report_name=f"download_task_{task.id}",
        )
        task.quality_report_json = str(report.json_path)
        task.quality_report_csv = str(report.csv_path)
        await self.session.commit()
        await persist_quality_findings(
            self.session,
            task_id=task.id,
            period=task.period,
            findings=findings,
            replace_symbols=handled_symbols,
        )
        if self.refresh_views:
            await asyncio.to_thread(refresh_market_views, data_root=self.store.root)
        await self.session.refresh(task)
        return task

    async def _run_item(
        self,
        task: DownloadTask,
        item: DownloadTaskItem,
        expected_days: list[str],
    ) -> list[QualityFinding]:
        last_error = ""
        last_assessment: DownloadFailureAssessment | None = None
        instrument = await self.session.get(Instrument, item.symbol)
        facts = self._instrument_facts(instrument)
        for attempt in range(item.attempts, task.max_retries + 1):
            item.status = "running"
            item.attempts = attempt + 1
            item.started_at = item.started_at or datetime.now()
            await self.session.commit()
            try:
                start_date = self._resume_start(task, item.symbol, expected_days)
                await asyncio.to_thread(
                    self.gateway.download_history,
                    item.symbol,
                    period=task.period,
                    start_time=start_date,
                    end_time=task.end_date,
                    incrementally=True,
                )
                frames = await asyncio.to_thread(
                    self.gateway.local_bars,
                    [item.symbol],
                    fields=TICK_FIELDS if task.period == "tick" else OHLCV_FIELDS,
                    period=task.period,
                    start_time=start_date,
                    end_time=task.end_date,
                    dividend_type="none" if task.period == "tick" else task.adjust,
                    fill_data=False,
                )
                raw = frames.get(item.symbol)
                if raw is None or raw.empty:
                    raise ValueError("miniQMT 返回空行情")
                result = await asyncio.to_thread(
                    self.pipeline.process,
                    raw,
                    symbol=item.symbol,
                    period=task.period,
                    adjust=task.adjust,
                    expected_days=expected_days,
                    asset_type=facts.asset_type if facts else None,
                )
                for metadata in result.files:
                    await upsert_data_file(self.session, metadata)
                item.status = "succeeded"
                item.rows = result.trusted_rows
                item.start_date = start_date
                item.end_date = task.end_date
                item.error_message = None
                item.failure_category = None
                item.failure_details_json = "{}"
                item.retryable = True
                item.next_retry_at = None
                item.quarantined_at = None
                item.finished_at = datetime.now()
                await self.session.commit()
                return result.findings
            except Exception as exc:
                last_error = str(exc)
                provider_detail = await self._provider_detail(item.symbol, exc)
                last_assessment = classify_download_failure(
                    exc,
                    symbol=item.symbol,
                    start_date=task.start_date,
                    end_date=task.end_date,
                    instrument=facts,
                    provider_detail=provider_detail,
                )
                item.error_message = last_error[:1024]
                item.failure_category = last_assessment.category.value
                item.failure_details_json = json.dumps(
                    last_assessment.details, ensure_ascii=False, default=str
                )
                item.retryable = last_assessment.retryable
                item.next_retry_at = last_assessment.next_retry_at
                item.quarantined_at = datetime.now() if last_assessment.quarantine else None
                await self.session.commit()
                if last_assessment.quarantine:
                    break
                if attempt < task.max_retries:
                    delay = self.settings.market_download_retry_backoff_seconds * (2**attempt)
                    await asyncio.sleep(delay)
        item.status = "failed"
        item.finished_at = datetime.now()
        item.error_message = last_error[:1024]
        await self.session.commit()
        category = last_assessment.category.value if last_assessment else "provider_error"
        details = last_assessment.details if last_assessment else None
        return [QualityFinding(item.symbol, category, "error", last_error, details=details)]

    async def _provider_detail(self, symbol: str, error: Exception) -> dict[str, Any] | None:
        if not isinstance(error, ValueError):
            return None
        try:
            return await asyncio.to_thread(self.gateway.instrument_detail, symbol)
        except Exception:
            return None

    @staticmethod
    def _instrument_facts(instrument: Instrument | None) -> InstrumentFacts | None:
        if instrument is None:
            return None
        return InstrumentFacts(
            symbol=instrument.symbol,
            name=instrument.name,
            exchange=instrument.exchange,
            asset_type=instrument.asset_type,
            list_date=instrument.list_date,
            delist_date=instrument.delist_date,
            is_active=instrument.is_active,
        )

    @staticmethod
    def _stored_failure_finding(item: DownloadTaskItem) -> QualityFinding:
        try:
            details = json.loads(item.failure_details_json or "{}")
        except json.JSONDecodeError:
            details = {}
        return QualityFinding(
            item.symbol,
            item.failure_category or "provider_error",
            "error",
            item.error_message or "下载失败",
            details=details,
        )

    def _resume_start(
        self,
        task: DownloadTask,
        symbol: str,
        expected_days: list[str] | None = None,
    ) -> str:
        path = self.store.path_for(symbol, task.period, task.adjust, "trusted")
        if not path.exists():
            return task.start_date
        frame = self.store.read(symbol, task.period, task.adjust, "trusted")
        if frame.empty or "datetime" not in frame:
            return task.start_date
        first = datetime.fromisoformat(str(frame["datetime"].min())).date()
        if task.start_date < first.strftime("%Y%m%d"):
            # The request extends further into history than the current local file.
            # Start at the requested boundary so incremental mode backfills the gap.
            return task.start_date
        last = datetime.fromisoformat(str(frame["datetime"].max())).date()
        if expected_days:
            actual = set(
                pd.to_datetime(frame["datetime"], errors="coerce").dropna().dt.strftime("%Y%m%d")
            )
            first_text = first.strftime("%Y%m%d")
            last_text = last.strftime("%Y%m%d")
            missing = [
                day
                for day in expected_days
                if max(task.start_date, first_text) <= day <= min(task.end_date, last_text)
                and day not in actual
            ]
            if missing:
                missing_date = datetime.strptime(missing[0], "%Y%m%d").date()
                return max(task.start_date, (missing_date - timedelta(days=7)).strftime("%Y%m%d"))
        overlap = (last - timedelta(days=7)).strftime("%Y%m%d")
        return max(task.start_date, overlap)

    async def _expected_days(self, start_date: str, end_date: str) -> list[str]:
        try:
            return await asyncio.to_thread(self.gateway.trading_days, start_date, end_date, "SH")
        except Exception:
            return []

    async def _update_progress(self, task: DownloadTask) -> None:
        items = await list_task_items(self.session, task.id)
        task.succeeded_items = sum(item.status == "succeeded" for item in items)
        task.failed_items = sum(item.status == "failed" for item in items)
        task.completed_items = task.succeeded_items + task.failed_items
        task.progress = round(task.completed_items / max(len(items), 1) * 100.0, 2)
        await self.session.commit()


def task_to_dict(task: DownloadTask, items: list[DownloadTaskItem] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": task.id,
        "idempotency_key": task.idempotency_key,
        "symbols": json.loads(task.symbols_json or "[]"),
        "period": task.period,
        "adjust": task.adjust,
        "start_date": task.start_date,
        "end_date": task.end_date,
        "status": task.status,
        "total_items": task.total_items,
        "completed_items": task.completed_items,
        "succeeded_items": task.succeeded_items,
        "failed_items": task.failed_items,
        "progress": task.progress,
        "quality_report_json": task.quality_report_json,
        "quality_report_csv": task.quality_report_csv,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }
    if items is not None:
        result["items"] = [
            {
                "symbol": item.symbol,
                "status": item.status,
                "attempts": item.attempts,
                "rows": item.rows,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "error_message": item.error_message,
                "failure_category": item.failure_category,
                "failure_details": json.loads(item.failure_details_json or "{}"),
                "retryable": item.retryable,
                "next_retry_at": item.next_retry_at,
                "quarantined_at": item.quarantined_at,
            }
            for item in items
        ]
    return result
