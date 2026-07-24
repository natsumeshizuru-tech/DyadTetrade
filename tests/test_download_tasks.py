from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.db.models import DownloadTask, DownloadTaskItem, Instrument, QualityIssue
from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.db.repositories import (
    list_task_items,
    persist_quality_findings,
    recover_interrupted_downloads,
    reset_failed_download_items,
)
from dyadtetrade.db.session import Base
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.market_data.quality import QualityFinding
from dyadtetrade.modules.market_data.tasks import DownloadTaskRunner
from tests.test_market_data import FakeXtData


class FlakyXtData(FakeXtData):
    def __init__(self) -> None:
        self.download_calls = 0

    def download_history_data(self, **_: Any) -> None:
        self.download_calls += 1
        if self.download_calls == 1:
            raise RuntimeError("temporary failure")


class IsolatedFailureXtData(FakeXtData):
    def download_history_data(self, **kwargs: Any) -> None:
        if kwargs["stock_code"] == "000001.SZ":
            raise RuntimeError("permanent failure")


class UnsupportedInstrumentXtData(FakeXtData):
    def __init__(self) -> None:
        self.download_calls = 0

    def download_history_data(self, **_: Any) -> None:
        self.download_calls += 1

    def get_market_data_ex(self, **kwargs: Any) -> dict[str, pd.DataFrame]:
        return {kwargs["stock_list"][0]: pd.DataFrame()}

    def get_instrument_detail(self, *_: Any) -> dict[str, object]:
        return {"InstrumentName": "认购款", "OpenDate": "0", "ExpireDate": "99999999"}


@pytest.mark.asyncio
async def test_download_task_retries_is_idempotent_and_persists_progress(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'task.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    fake = FlakyXtData()
    async with factory() as session:
        runner = DownloadTaskRunner(
            session,
            gateway=XtQuantGateway(fake),
            store=ParquetStore(tmp_path / "data"),
            report_root=tmp_path / "reports",
            refresh_views=False,
        )
        task, created = await runner.create(
            symbols=["600519.SH"],
            start_date="20260709",
            end_date="20260710",
            period="1d",
            adjust="front",
            max_retries=1,
        )
        duplicate, duplicate_created = await runner.create(
            symbols=["600519.SH"],
            start_date="20260709",
            end_date="20260710",
            period="1d",
            adjust="front",
            max_retries=1,
        )
        assert created and not duplicate_created and duplicate.id == task.id
        completed = await runner.run(task.id)
        items = await list_task_items(session, task.id)
        assert completed.status == "succeeded"
        assert completed.progress == 100
        assert items[0].attempts == 2
        assert items[0].status == "succeeded"
        assert completed.quality_report_json is not None
        assert Path(completed.quality_report_json).exists()

        completed.status = "running"
        completed.updated_at = datetime.now() - timedelta(minutes=10)
        await session.commit()
        assert await recover_interrupted_downloads(session) == 1
        recovered = await session.get(DownloadTask, completed.id)
        assert recovered is not None and recovered.status == "pending"
    await engine.dispose()


@pytest.mark.asyncio
async def test_single_symbol_failure_does_not_abort_batch(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'isolated.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        runner = DownloadTaskRunner(
            session,
            gateway=XtQuantGateway(IsolatedFailureXtData()),
            store=ParquetStore(tmp_path / "data"),
            report_root=tmp_path / "reports",
            refresh_views=False,
        )
        task, _ = await runner.create(
            symbols=["000001.SZ", "600519.SH"],
            start_date="20260709",
            end_date="20260710",
            period="1d",
            adjust="front",
            max_retries=1,
        )
        completed = await runner.run(task.id)
        items = {item.symbol: item for item in await list_task_items(session, task.id)}
        assert completed.status == "partial"
        assert items["000001.SZ"].status == "failed"
        assert items["600519.SH"].status == "succeeded"
        assert completed.succeeded_items == completed.failed_items == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_permanent_failure_is_classified_and_quarantined_without_retry(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quarantine.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    fake = UnsupportedInstrumentXtData()
    async with factory() as session:
        session.add(
            Instrument(
                symbol="561414.SH",
                name="认购款",
                exchange="SH",
                asset_type="etf",
                is_active=True,
            )
        )
        await session.commit()
        runner = DownloadTaskRunner(
            session,
            gateway=XtQuantGateway(fake),
            store=ParquetStore(tmp_path / "data"),
            report_root=tmp_path / "reports",
            refresh_views=False,
        )
        task, _ = await runner.create(
            symbols=["561414.SH"],
            start_date="20260709",
            end_date="20260710",
            period="1d",
            adjust="front",
            max_retries=3,
        )

        completed = await runner.run(task.id)
        item = (await list_task_items(session, task.id))[0]

        assert completed.status == "failed"
        assert fake.download_calls == 1
        assert item.attempts == 1
        assert item.failure_category == "unsupported_instrument"
        assert item.retryable is False
        assert item.quarantined_at is not None
        assert item.next_retry_at is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_item_reset_excludes_quarantine_unless_explicitly_requested(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reset-policy.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        task = DownloadTask(
            symbols_json='["600519.SH", "561414.SH"]',
            period="1d",
            adjust="front",
            start_date="20260709",
            end_date="20260710",
            total_items=2,
        )
        session.add(task)
        await session.flush()
        session.add_all(
            [
                DownloadTaskItem(
                    task_id=task.id,
                    symbol="600519.SH",
                    status="failed",
                    retryable=True,
                    failure_category="provider_error",
                ),
                DownloadTaskItem(
                    task_id=task.id,
                    symbol="561414.SH",
                    status="failed",
                    retryable=False,
                    failure_category="unsupported_instrument",
                    quarantined_at=datetime(2026, 7, 13, 20, 0),
                ),
            ]
        )
        await session.commit()

        assert await reset_failed_download_items(session, task.id) == 1
        items = {item.symbol: item for item in await list_task_items(session, task.id)}
        assert items["600519.SH"].status == "pending"
        assert items["561414.SH"].status == "failed"

        assert await reset_failed_download_items(session, task.id, include_quarantined=True) == 1
        items = {item.symbol: item for item in await list_task_items(session, task.id)}
        assert items["561414.SH"].status == "pending"
        assert items["561414.SH"].quarantined_at is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_partial_rerun_preserves_quality_findings_for_untouched_symbols(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quality-replace.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        task = DownloadTask(
            symbols_json='["600519.SH", "000001.SZ"]',
            period="1d",
            adjust="front",
            start_date="20260709",
            end_date="20260710",
            total_items=2,
        )
        session.add(task)
        await session.flush()
        session.add_all(
            [
                QualityIssue(
                    task_id=task.id,
                    symbol="600519.SH",
                    period="1d",
                    check_code="missing_trading_days",
                    severity="warning",
                    message="保留",
                ),
                QualityIssue(
                    task_id=task.id,
                    symbol="000001.SZ",
                    period="1d",
                    check_code="download_failed",
                    severity="error",
                    message="替换",
                ),
            ]
        )
        await session.commit()

        await persist_quality_findings(
            session,
            task_id=task.id,
            period="1d",
            findings=[
                QualityFinding(
                    "000001.SZ",
                    "provider_error",
                    "error",
                    "新分类",
                )
            ],
            replace_symbols={"000001.SZ"},
        )
        issues = (
            (await session.execute(select(QualityIssue).order_by(QualityIssue.symbol.desc())))
            .scalars()
            .all()
        )

        assert [(item.symbol, item.check_code) for item in issues] == [
            ("600519.SH", "missing_trading_days"),
            ("000001.SZ", "provider_error"),
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_resume_start_backfills_an_earlier_requested_boundary(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = ParquetStore(tmp_path / "data")
    store.write(
        pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-07-06", "2026-07-10"]),
                "symbol": ["510300.SH", "510300.SH"],
                "close": [4.0, 4.1],
            }
        ),
        symbol="510300.SH",
        period="1d",
        adjust="front",
        layer="trusted",
    )
    async with factory() as session:
        runner = DownloadTaskRunner(
            session,
            gateway=XtQuantGateway(FakeXtData()),
            store=store,
            report_root=tmp_path / "reports",
            refresh_views=False,
        )
        task = DownloadTask(
            start_date="20260501",
            end_date="20260710",
            period="1d",
            adjust="front",
        )
        assert runner._resume_start(task, "510300.SH") == "20260501"


@pytest.mark.asyncio
async def test_resume_start_rewinds_to_an_internal_missing_trading_day(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = ParquetStore(tmp_path / "data")
    frame = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-06-01", "2026-06-15", "2026-06-16", "2026-06-19"]),
            "symbol": ["510300.SH"] * 4,
            "open": [4.0] * 4,
            "high": [4.1] * 4,
            "low": [3.9] * 4,
            "close": [4.0] * 4,
            "volume": [100] * 4,
            "amount": [400] * 4,
        }
    )
    store.write(
        frame,
        symbol="510300.SH",
        period="1d",
        adjust="front",
        layer="trusted",
    )
    async with factory() as session:
        runner = DownloadTaskRunner(
            session,
            gateway=XtQuantGateway(FakeXtData()),
            store=store,
            refresh_views=False,
        )
        task, _ = await runner.create(
            symbols=["510300.SH"],
            start_date="20260601",
            end_date="20260630",
            period="1d",
            adjust="front",
        )
        expected = [
            "20260601",
            "20260615",
            "20260616",
            "20260617",
            "20260618",
            "20260619",
        ]
        assert runner._resume_start(task, "510300.SH", expected) == "20260610"
    await engine.dispose()
