from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.models import (
    DataFile,
    DownloadTask,
    DownloadTaskItem,
    Instrument,
    QualityIssue,
    Sector,
    SectorMember,
    SectorMemberSnapshot,
    StockPool,
    TradingDay,
)
from dyadtetrade.db.parquet_store import ParquetMetadata
from dyadtetrade.modules.market_data.quality import QualityFinding


async def upsert_data_file(session: AsyncSession, metadata: ParquetMetadata) -> DataFile:
    path = str(metadata.file_path)
    model = await session.scalar(select(DataFile).where(DataFile.file_path == path))
    if model is None:
        model = DataFile(file_path=path)
        session.add(model)

    model.symbol = metadata.symbol
    model.period = metadata.period
    model.adjust = metadata.adjust
    model.layer = metadata.layer
    model.start_date = metadata.start_date
    model.end_date = metadata.end_date
    model.rows = metadata.rows
    model.file_size = metadata.file_size
    model.checksum = metadata.checksum
    model.schema_version = metadata.schema_version
    model.pipeline_version = metadata.pipeline_version
    model.imported_from = metadata.imported_from
    await session.commit()
    await session.refresh(model)
    return model


async def upsert_instruments(session: AsyncSession, symbols: list[str]) -> None:
    existing = set(
        (
            await session.scalars(select(Instrument.symbol).where(Instrument.symbol.in_(symbols)))
        ).all()
    )
    for symbol in symbols:
        if symbol not in existing:
            code, exchange = symbol.split(".")
            session.add(
                Instrument(
                    symbol=symbol,
                    name=None,
                    exchange=exchange,
                    asset_type="stock",
                    is_active=True,
                    source="xtquant",
                )
            )
    await session.commit()


async def upsert_instrument_records(session: AsyncSession, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    symbols = [str(record["symbol"]) for record in records]
    existing: dict[str, Instrument] = {}
    for offset in range(0, len(symbols), 500):
        batch = symbols[offset : offset + 500]
        existing.update(
            {
                item.symbol: item
                for item in (
                    await session.scalars(select(Instrument).where(Instrument.symbol.in_(batch)))
                ).all()
            }
        )
    for record in records:
        symbol = str(record["symbol"])
        model = existing.get(symbol)
        if model is None:
            model = Instrument(symbol=symbol, exchange=symbol.split(".")[1])
            session.add(model)
        for field in (
            "name",
            "exchange",
            "asset_type",
            "industry",
            "list_date",
            "delist_date",
            "lot_size",
            "price_tick",
            "price_decimals",
            "quantity_decimals",
            "currency",
            "money_decimals",
            "is_active",
            "source",
        ):
            if field in record and record[field] is not None:
                setattr(model, field, record[field])
        model.updated_at = datetime.now()
    await session.commit()


async def deactivate_stale_xtquant_instruments(
    session: AsyncSession, active_symbols: list[str]
) -> int:
    if not active_symbols:
        return 0
    stale = (
        await session.scalars(
            select(Instrument).where(
                Instrument.source == "xtquant",
                Instrument.is_active.is_(True),
                Instrument.symbol.not_in(active_symbols),
            )
        )
    ).all()
    for instrument in stale:
        instrument.is_active = False
        instrument.updated_at = datetime.now()
    await session.commit()
    return len(stale)


async def replace_sector_members(
    session: AsyncSession,
    *,
    sector_name: str,
    category: str,
    symbols: list[str],
) -> None:
    sector = await session.scalar(select(Sector).where(Sector.name == sector_name))
    if sector is None:
        sector = Sector(name=sector_name, category=category)
        session.add(sector)
        await session.flush()
    else:
        sector.category = category
        sector.updated_at = datetime.now()
    await session.execute(delete(SectorMember).where(SectorMember.sector_id == sector.id))
    session.add_all(
        SectorMember(sector_id=sector.id, symbol=symbol) for symbol in sorted(set(symbols))
    )
    snapshot_date = datetime.now().strftime("%Y%m%d")
    await session.execute(
        delete(SectorMemberSnapshot).where(
            SectorMemberSnapshot.sector_id == sector.id,
            SectorMemberSnapshot.as_of_date == snapshot_date,
        )
    )
    session.add_all(
        SectorMemberSnapshot(
            sector_id=sector.id,
            symbol=symbol,
            as_of_date=snapshot_date,
        )
        for symbol in sorted(set(symbols))
    )
    await session.commit()


async def assign_industry(session: AsyncSession, *, symbols: list[str], industry: str) -> None:
    for offset in range(0, len(symbols), 500):
        batch = symbols[offset : offset + 500]
        await session.execute(
            update(Instrument)
            .where(Instrument.symbol.in_(batch))
            .values(industry=industry, updated_at=datetime.now())
        )
    await session.commit()


async def clear_xtquant_industries(session: AsyncSession) -> None:
    await session.execute(
        update(Instrument)
        .where(Instrument.source == "xtquant", Instrument.is_active.is_(True))
        .values(industry=None)
    )
    await session.commit()


async def upsert_trading_days(
    session: AsyncSession,
    market: str,
    days: list[str],
    source: str = "xtquant",
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    if start_date is not None and end_date is not None:
        statement = delete(TradingDay).where(
            TradingDay.market == market,
            TradingDay.trade_date >= start_date,
            TradingDay.trade_date <= end_date,
        )
        if days:
            statement = statement.where(TradingDay.trade_date.not_in(days))
        await session.execute(statement)
    existing = set(
        (
            await session.scalars(
                select(TradingDay.trade_date).where(
                    TradingDay.market == market,
                    TradingDay.trade_date.in_(days),
                )
            )
        ).all()
    )
    for day in days:
        if day not in existing:
            session.add(TradingDay(market=market, trade_date=day, source=source))
    await session.commit()


async def create_or_get_download_task(
    session: AsyncSession,
    *,
    idempotency_key: str,
    symbols: list[str],
    period: str,
    adjust: str,
    start_date: str,
    end_date: str,
    max_retries: int,
) -> tuple[DownloadTask, bool]:
    existing = await session.scalar(
        select(DownloadTask).where(DownloadTask.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing, False
    task = DownloadTask(
        symbol=symbols[0] if len(symbols) == 1 else "*",
        idempotency_key=idempotency_key,
        symbols_json=json.dumps(symbols),
        period=period,
        adjust=adjust,
        start_date=start_date,
        end_date=end_date,
        status="pending",
        total_items=len(symbols),
        max_retries=max_retries,
    )
    session.add(task)
    await session.flush()
    session.add_all(DownloadTaskItem(task_id=task.id, symbol=symbol) for symbol in symbols)
    await session.commit()
    await session.refresh(task)
    return task, True


async def get_download_task(session: AsyncSession, task_id: int) -> DownloadTask | None:
    return await session.get(DownloadTask, task_id)


async def list_download_tasks(session: AsyncSession, *, limit: int = 50) -> list[DownloadTask]:
    return list(
        (
            await session.scalars(
                select(DownloadTask).order_by(DownloadTask.id.desc()).limit(limit)
            )
        ).all()
    )


async def list_task_items(session: AsyncSession, task_id: int) -> list[DownloadTaskItem]:
    return list(
        (
            await session.scalars(
                select(DownloadTaskItem)
                .where(DownloadTaskItem.task_id == task_id)
                .order_by(DownloadTaskItem.id)
            )
        ).all()
    )


async def recover_interrupted_downloads(session: AsyncSession) -> int:
    stale_before = datetime.now() - timedelta(minutes=5)
    running = int(
        await session.scalar(
            select(func.count())
            .select_from(DownloadTask)
            .where(DownloadTask.status == "running", DownloadTask.updated_at < stale_before)
        )
        or 0
    )
    await session.execute(
        update(DownloadTask)
        .where(DownloadTask.status == "running", DownloadTask.updated_at < stale_before)
        .values(status="pending")
    )
    await session.execute(
        update(DownloadTaskItem)
        .where(
            DownloadTaskItem.status == "running",
            DownloadTaskItem.updated_at < stale_before,
        )
        .values(status="pending", error_message="进程中断，等待恢复")
    )
    await session.commit()
    return running


async def reset_failed_download_items(
    session: AsyncSession,
    task_id: int,
    *,
    include_quarantined: bool = False,
    now: datetime | None = None,
) -> int:
    conditions = [
        DownloadTaskItem.task_id == task_id,
        DownloadTaskItem.status == "failed",
    ]
    if not include_quarantined:
        conditions.extend(
            [
                DownloadTaskItem.retryable.is_(True),
                DownloadTaskItem.quarantined_at.is_(None),
                (DownloadTaskItem.next_retry_at.is_(None))
                | (DownloadTaskItem.next_retry_at <= (now or datetime.now())),
            ]
        )
    result = await session.execute(
        update(DownloadTaskItem)
        .where(*conditions)
        .values(
            status="pending",
            attempts=0,
            error_message=None,
            retryable=True,
            next_retry_at=None,
            quarantined_at=None,
            finished_at=None,
        )
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def persist_quality_findings(
    session: AsyncSession,
    *,
    task_id: int | None,
    period: str,
    findings: list[QualityFinding],
    replace_symbols: set[str] | None = None,
) -> None:
    if task_id is not None:
        if replace_symbols is None:
            await session.execute(delete(QualityIssue).where(QualityIssue.task_id == task_id))
        elif replace_symbols:
            await session.execute(
                delete(QualityIssue).where(
                    QualityIssue.task_id == task_id,
                    QualityIssue.symbol.in_(replace_symbols),
                )
            )
    session.add_all(
        QualityIssue(
            task_id=task_id,
            symbol=item.symbol,
            period=period,
            check_code=item.check_code,
            severity=item.severity,
            trade_date=item.trade_date,
            message=item.message,
            details_json=json.dumps(item.details or {}, ensure_ascii=False),
        )
        for item in findings
    )
    await session.commit()


async def data_center_summary(session: AsyncSession) -> dict[str, Any]:
    instruments = int(
        await session.scalar(
            select(func.count()).select_from(Instrument).where(Instrument.is_active.is_(True))
        )
        or 0
    )
    pools = int(await session.scalar(select(func.count()).select_from(StockPool)) or 0)
    tasks = int(await session.scalar(select(func.count()).select_from(DownloadTask)) or 0)
    running = int(
        await session.scalar(
            select(func.count()).select_from(DownloadTask).where(DownloadTask.status == "running")
        )
        or 0
    )
    issues = int(await session.scalar(select(func.count()).select_from(QualityIssue)) or 0)
    covered = int(
        await session.scalar(
            select(func.count(func.distinct(DataFile.symbol)))
            .join(Instrument, Instrument.symbol == DataFile.symbol)
            .where(
                DataFile.layer == "trusted",
                DataFile.period == "1d",
                Instrument.is_active.is_(True),
            )
        )
        or 0
    )
    coverage = round((covered / instruments * 100.0), 2) if instruments else 0.0
    latest_date = await session.scalar(
        select(func.max(DataFile.end_date))
        .join(Instrument, Instrument.symbol == DataFile.symbol)
        .where(
            DataFile.layer == "trusted",
            DataFile.period == "1d",
            Instrument.is_active.is_(True),
        )
    )
    latest_covered = 0
    if latest_date is not None:
        latest_covered = int(
            await session.scalar(
                select(func.count(func.distinct(DataFile.symbol)))
                .join(Instrument, Instrument.symbol == DataFile.symbol)
                .where(
                    DataFile.layer == "trusted",
                    DataFile.period == "1d",
                    DataFile.end_date == latest_date,
                    Instrument.is_active.is_(True),
                )
            )
            or 0
        )
    failed_items = int(
        await session.scalar(
            select(func.count())
            .select_from(DownloadTaskItem)
            .where(DownloadTaskItem.status == "failed")
        )
        or 0
    )
    retryable_failed_items = int(
        await session.scalar(
            select(func.count())
            .select_from(DownloadTaskItem)
            .where(
                DownloadTaskItem.status == "failed",
                DownloadTaskItem.retryable.is_(True),
                DownloadTaskItem.quarantined_at.is_(None),
            )
        )
        or 0
    )
    quarantined_items = int(
        await session.scalar(
            select(func.count())
            .select_from(DownloadTaskItem)
            .where(
                DownloadTaskItem.status == "failed",
                DownloadTaskItem.quarantined_at.is_not(None),
            )
        )
        or 0
    )
    category_rows = (
        await session.execute(
            select(
                DownloadTaskItem.failure_category,
                func.count(DownloadTaskItem.id),
            )
            .where(DownloadTaskItem.status == "failed")
            .group_by(DownloadTaskItem.failure_category)
        )
    ).all()
    failure_category_counts = {
        str(category or "provider_error"): int(count) for category, count in category_rows
    }
    error_issues = int(
        await session.scalar(
            select(func.count()).select_from(QualityIssue).where(QualityIssue.severity == "error")
        )
        or 0
    )
    last_updated = await session.scalar(select(func.max(DataFile.updated_at)))
    return {
        "instrument_count": instruments,
        "stock_pool_count": pools,
        "task_count": tasks,
        "running_task_count": running,
        "quality_issue_count": issues,
        "covered_instrument_count": covered,
        "coverage_percent": coverage,
        "latest_trade_date": latest_date,
        "latest_trade_date_covered_count": latest_covered,
        "missing_instrument_count": max(instruments - covered, 0),
        "empty_or_error_count": error_issues,
        "failed_download_item_count": failed_items,
        "retryable_failed_download_item_count": retryable_failed_items,
        "quarantined_download_item_count": quarantined_items,
        "failure_category_counts": failure_category_counts,
        "last_updated_at": last_updated.isoformat() if last_updated else None,
    }
