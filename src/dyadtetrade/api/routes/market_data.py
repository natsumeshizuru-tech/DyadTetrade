from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from dyadtetrade.core.symbols import normalize_symbol
from dyadtetrade.db.duckdb_store import query_bars, refresh_market_views
from dyadtetrade.db.models import QualityIssue
from dyadtetrade.db.repositories import (
    data_center_summary,
    get_download_task,
    list_download_tasks,
    list_task_items,
    persist_quality_findings,
    recover_interrupted_downloads,
    reset_failed_download_items,
    upsert_data_file,
    upsert_instruments,
    upsert_trading_days,
)
from dyadtetrade.db.session import get_session
from dyadtetrade.integrations.xtquant import MiniQmtUnavailableError, XtQuantGateway
from dyadtetrade.modules.market_data.importer import LegacyMarketDataImporter
from dyadtetrade.modules.market_data.schemas import (
    CalendarResult,
    DataCenterSummary,
    DownloadResult,
    DownloadTaskCreate,
    DownloadTaskResult,
    HistoryDownloadRequest,
    LegacyImportRequest,
    StoredFile,
    UniverseResult,
)
from dyadtetrade.modules.market_data.service import MarketDataService, metadata_to_dict
from dyadtetrade.modules.market_data.stitching import MarketBarStitchingService
from dyadtetrade.modules.market_data.tasks import DownloadTaskRunner, task_to_dict

router = APIRouter(prefix="/market", tags=["market-data"])


@lru_cache
def get_gateway() -> XtQuantGateway:
    return XtQuantGateway()


GatewayDep = Annotated[XtQuantGateway, Depends(get_gateway)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_service(gateway: GatewayDep) -> MarketDataService:
    return MarketDataService(gateway=gateway)


ServiceDep = Annotated[MarketDataService, Depends(get_service)]
stitching_service = MarketBarStitchingService()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def service_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/status")
async def market_status(gateway: GatewayDep) -> dict[str, Any]:
    status = await run_in_threadpool(gateway.status)
    return cast(dict[str, Any], json_safe(status.__dict__))


@router.get("/sectors", response_model=list[str])
async def sectors(gateway: GatewayDep) -> list[str]:
    try:
        return await run_in_threadpool(gateway.list_sectors)
    except MiniQmtUnavailableError as exc:
        raise service_unavailable(exc) from exc


@router.get("/universe", response_model=UniverseResult)
async def universe(
    gateway: GatewayDep,
    session: SessionDep,
    include_bj: bool = True,
    persist: bool = True,
) -> UniverseResult:
    try:
        symbols = await run_in_threadpool(
            lambda: gateway.list_a_share_universe(include_bj=include_bj)
        )
    except MiniQmtUnavailableError as exc:
        raise service_unavailable(exc) from exc
    if persist:
        await upsert_instruments(session, symbols)
    return UniverseResult(count=len(symbols), symbols=symbols)


@router.get("/instruments/{symbol}")
async def instrument(
    symbol: str,
    gateway: GatewayDep,
) -> dict[str, Any]:
    try:
        detail = await run_in_threadpool(gateway.instrument_detail, normalize_symbol(symbol))
        return cast(dict[str, Any], json_safe(detail))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MiniQmtUnavailableError as exc:
        raise service_unavailable(exc) from exc


@router.get("/quotes")
async def quotes(
    gateway: GatewayDep,
    symbols: Annotated[str, Query(description="逗号分隔证券代码，最多 50 个")],
) -> dict[str, Any]:
    try:
        normalized = [normalize_symbol(item) for item in symbols.split(",") if item.strip()]
        if not 1 <= len(normalized) <= 50:
            raise ValueError("证券代码数量必须为 1-50")
        ticks = await run_in_threadpool(gateway.full_tick, normalized)
        return cast(dict[str, Any], json_safe(ticks))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MiniQmtUnavailableError as exc:
        raise service_unavailable(exc) from exc


@router.get("/calendar", response_model=CalendarResult)
async def calendar(
    gateway: GatewayDep,
    session: SessionDep,
    start_date: Annotated[str, Query(pattern=r"^\d{8}$")],
    end_date: Annotated[str, Query(pattern=r"^\d{8}$")],
    market: Literal["SH", "SZ", "BJ"] = "SH",
    persist: bool = True,
) -> CalendarResult:
    try:
        days = await run_in_threadpool(gateway.trading_days, start_date, end_date, market)
    except MiniQmtUnavailableError as exc:
        raise service_unavailable(exc) from exc
    if persist:
        await upsert_trading_days(
            session,
            market,
            days,
            start_date=start_date,
            end_date=end_date,
        )
    return CalendarResult(market=market, count=len(days), days=days)


@router.post("/history/download", response_model=DownloadResult)
async def download_history(
    request: HistoryDownloadRequest,
    service: ServiceDep,
    session: SessionDep,
) -> DownloadResult:
    files, errors = await run_in_threadpool(
        lambda: service.download_and_store(
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            period=request.period,
            adjust=request.adjust,
            incrementally=request.incrementally,
        )
    )
    for metadata in files:
        await upsert_data_file(session, metadata)
    return DownloadResult(
        files=[StoredFile.model_validate(metadata_to_dict(item)) for item in files],
        errors=errors,
    )


@router.post("/history/tasks", response_model=DownloadTaskResult)
async def create_history_task(
    request: DownloadTaskCreate,
    gateway: GatewayDep,
    session: SessionDep,
) -> DownloadTaskResult:
    runner = DownloadTaskRunner(session, gateway=gateway)
    task, _ = await runner.create(
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        period=request.period,
        adjust=request.adjust,
        max_retries=request.max_retries,
    )
    if request.execute_now:
        task = await runner.run(task.id)
    return DownloadTaskResult.model_validate(task_to_dict(task))


@router.get("/history/tasks", response_model=list[DownloadTaskResult])
async def history_tasks(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DownloadTaskResult]:
    tasks = await list_download_tasks(session, limit=limit)
    return [DownloadTaskResult.model_validate(task_to_dict(task)) for task in tasks]


@router.post("/history/tasks/recover")
async def recover_history_tasks(session: SessionDep) -> dict[str, int]:
    return {"recovered": await recover_interrupted_downloads(session)}


@router.get("/history/tasks/{task_id}", response_model=DownloadTaskResult)
async def history_task(task_id: int, session: SessionDep) -> DownloadTaskResult:
    task = await get_download_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="下载任务不存在")
    items = await list_task_items(session, task_id)
    return DownloadTaskResult.model_validate(task_to_dict(task, items))


@router.post("/history/tasks/{task_id}/run", response_model=DownloadTaskResult)
async def run_history_task(
    task_id: int,
    gateway: GatewayDep,
    session: SessionDep,
    retry_failed: bool = True,
    include_quarantined: bool = False,
) -> DownloadTaskResult:
    task = await get_download_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="下载任务不存在")
    if retry_failed:
        await reset_failed_download_items(
            session,
            task_id,
            include_quarantined=include_quarantined,
        )
    try:
        completed = await DownloadTaskRunner(session, gateway=gateway).run(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DownloadTaskResult.model_validate(task_to_dict(completed))


@router.get("/summary", response_model=DataCenterSummary)
async def summary(session: SessionDep) -> DataCenterSummary:
    return DataCenterSummary.model_validate(await data_center_summary(session))


@router.get("/quality/issues")
async def quality_issues(
    session: SessionDep,
    severity: Literal["warning", "error"] | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[dict[str, Any]]:
    statement = select(QualityIssue).order_by(QualityIssue.id.desc()).limit(limit)
    if severity:
        statement = statement.where(QualityIssue.severity == severity)
    issues = (await session.scalars(statement)).all()
    return [
        {
            "id": issue.id,
            "task_id": issue.task_id,
            "symbol": issue.symbol,
            "check_code": issue.check_code,
            "severity": issue.severity,
            "trade_date": issue.trade_date,
            "message": issue.message,
            "details": json.loads(issue.details_json or "{}"),
            "created_at": issue.created_at,
        }
        for issue in issues
    ]


@router.get("/history/query")
async def duckdb_history_query(
    symbols: Annotated[str, Query(description="逗号分隔证券代码")],
    start_date: Annotated[str, Query(pattern=r"^\d{8}$")],
    end_date: Annotated[str, Query(pattern=r"^\d{8}$")],
    layer: Literal["trusted", "bt_ready"] = "trusted",
    period: str = "1d",
    adjust: Literal["none", "front", "back", "front_ratio", "back_ratio"] = "front",
    limit: Annotated[int, Query(ge=1, le=100000)] = 10000,
) -> dict[str, Any]:
    try:
        normalized = [normalize_symbol(item) for item in symbols.split(",") if item.strip()]
        records = await run_in_threadpool(
            lambda: query_bars(
                symbols=normalized,
                start_date=start_date,
                end_date=end_date,
                layer=layer,
                period=period,
                adjust=adjust,
                limit=limit,
            )
        )
        return {"count": len(records), "records": json_safe(records)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/candles")
async def stitched_candles(
    symbol: str,
    gateway: GatewayDep,
    start_date: Annotated[str, Query(pattern=r"^\d{8}$")],
    end_date: Annotated[str, Query(pattern=r"^\d{8}$")],
    period: Literal["1m", "5m", "15m", "30m", "1h", "1d"] = "1d",
    adjust_mode: Literal["none", "front", "back"] = "none",
    backfill: bool = True,
    limit: Annotated[int, Query(ge=1, le=10000)] = 5000,
) -> dict[str, object]:
    from dyadtetrade.api.routes.realtime import realtime_service

    normalized = normalize_symbol(symbol)
    backfill_error: str | None = None
    if backfill and adjust_mode == "none":
        today = datetime.now().strftime("%Y%m%d")
        try:
            await run_in_threadpool(
                lambda: stitching_service.backfill(
                    gateway,
                    symbol=normalized,
                    period=period,
                    start_time=max(start_date, (datetime.now().date()).strftime("%Y%m01")),
                    end_time=min(end_date, today),
                    adjust_mode="none",
                )
            )
            refresh_market_views()
        except Exception as exc:
            backfill_error = f"{type(exc).__name__}: {exc}"
    history = await run_in_threadpool(
        lambda: query_bars(
            symbols=[normalized],
            start_date=start_date,
            end_date=end_date,
            layer="trusted",
            period=period,
            adjust=adjust_mode,
            limit=limit,
        )
    )
    calendar_error: str | None = None
    try:
        trading_days = await run_in_threadpool(
            lambda: gateway.trading_days(
                start_date, min(end_date, datetime.now().strftime("%Y%m%d"))
            )
        )
    except Exception as exc:
        trading_days = None
        calendar_error = f"{type(exc).__name__}: {exc}"
    realtime_error: str | None = None
    if adjust_mode == "none" and trading_days is not None:
        try:
            realtime_service.aggregator.set_trading_days(trading_days)
            realtime_service.intraday_aggregator.set_trading_days(trading_days)
            raw_tick = await run_in_threadpool(lambda: gateway.full_tick([normalized]))
            payload = raw_tick.get(normalized)
            if isinstance(payload, dict):
                received_at = datetime.now(UTC)
                update = realtime_service.aggregator.update(normalized, payload, received_at)
                if update is not None:
                    realtime_service.intraday_aggregator.update(
                        normalized, update[0], payload, received_at
                    )
        except Exception as exc:
            realtime_error = f"{type(exc).__name__}: {exc}"
    realtime = (
        realtime_service.aggregator.bars().get(normalized)
        if period == "1d"
        else realtime_service.intraday_aggregator.current(normalized, period)
    )
    try:
        result = stitching_service.stitch(
            symbol=normalized,
            period=period,
            adjust_mode=adjust_mode,
            history=history,
            realtime=realtime,
            trading_days=trading_days,
            backfill_attempted=backfill and adjust_mode == "none",
            backfill_error=backfill_error,
            realtime_error=realtime_error,
            calendar_error=calendar_error,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return cast(dict[str, object], json_safe(asdict(result)))


@router.post("/history/import")
async def import_history(
    request: LegacyImportRequest,
    session: SessionDep,
) -> dict[str, Any]:
    importer = LegacyMarketDataImporter()
    result = await run_in_threadpool(
        importer.import_path,
        Path(request.source_path),
        period=request.period,
        adjust=request.adjust,
    )
    for metadata in result.files:
        await upsert_data_file(session, metadata)
    await persist_quality_findings(
        session, task_id=None, period=request.period, findings=result.findings
    )
    await run_in_threadpool(refresh_market_views)
    return {
        "source_files": result.source_files,
        "imported_symbols": result.imported_symbols,
        "errors": result.errors,
        "quality_findings": len(result.findings),
    }


@router.get("/history/{symbol}")
async def stored_history(
    symbol: str,
    service: ServiceDep,
    period: str = "1d",
    adjust: str = "front",
    layer: Literal["raw", "clean", "trusted", "bt_ready"] = "trusted",
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict[str, Any]:
    try:
        normalized = normalize_symbol(symbol)
        records = await run_in_threadpool(
            lambda: service.read_stored(
                symbol=normalized,
                period=period,
                adjust=adjust,
                layer=layer,
                limit=limit,
            )
        )
        return {"symbol": normalized, "count": len(records), "records": records}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"未找到本地行情文件：{exc}") from exc
