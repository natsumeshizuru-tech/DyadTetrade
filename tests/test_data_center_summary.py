from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.db.models import DataFile, DownloadTask, DownloadTaskItem, Instrument
from dyadtetrade.db.repositories import data_center_summary
from dyadtetrade.db.session import Base


@pytest.mark.asyncio
async def test_summary_uses_active_daily_universe_and_exposes_failure_policy(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'summary.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                Instrument(
                    symbol="600519.SH",
                    exchange="SH",
                    asset_type="stock",
                    is_active=True,
                ),
                Instrument(
                    symbol="000001.SZ",
                    exchange="SZ",
                    asset_type="stock",
                    is_active=True,
                ),
                Instrument(
                    symbol="600193.SH",
                    exchange="SH",
                    asset_type="stock",
                    is_active=False,
                ),
            ]
        )
        session.add_all(
            [
                _data_file("600519.SH", "1d", "active.parquet"),
                _data_file("600193.SH", "1d", "inactive.parquet"),
                _data_file("000001.SZ", "1m", "minute.parquet"),
                _data_file("300029.SZ", "1d", "orphan.parquet"),
            ]
        )
        task = DownloadTask(
            symbols_json='["600519.SH", "000001.SZ"]',
            period="1d",
            adjust="front",
            start_date="20260101",
            end_date="20260713",
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
                    failure_category="provider_error",
                    retryable=True,
                ),
                DownloadTaskItem(
                    task_id=task.id,
                    symbol="000001.SZ",
                    status="failed",
                    failure_category="no_market_data",
                    retryable=False,
                    quarantined_at=datetime(2026, 7, 13, 20, 0),
                ),
            ]
        )
        await session.commit()

        summary = await data_center_summary(session)

        assert summary["instrument_count"] == 2
        assert summary["covered_instrument_count"] == 1
        assert summary["missing_instrument_count"] == 1
        assert summary["coverage_percent"] == 50.0
        assert summary["failed_download_item_count"] == 2
        assert summary["retryable_failed_download_item_count"] == 1
        assert summary["quarantined_download_item_count"] == 1
        assert summary["failure_category_counts"] == {
            "no_market_data": 1,
            "provider_error": 1,
        }
    await engine.dispose()


def _data_file(symbol: str, period: str, filename: str) -> DataFile:
    return DataFile(
        symbol=symbol,
        period=period,
        adjust="front",
        layer="trusted",
        file_path=filename,
        start_date="20260101",
        end_date="20260713",
        rows=1,
        file_size=1,
    )
