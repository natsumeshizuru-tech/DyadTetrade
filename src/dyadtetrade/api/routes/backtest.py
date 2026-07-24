from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.models import BacktestResultRecord
from dyadtetrade.db.session import get_session
from dyadtetrade.modules.backtest.repository import get_task, list_tasks, result_detail
from dyadtetrade.modules.backtest.schemas import BacktestTaskCreate
from dyadtetrade.modules.backtest.service import BacktestService, task_to_dict

router = APIRouter(prefix="/backtests", tags=["backtests"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_backtest_service() -> BacktestService:
    return BacktestService()


ServiceDep = Annotated[BacktestService, Depends(get_backtest_service)]


@router.post("", status_code=201)
async def create_backtest(
    request: BacktestTaskCreate,
    session: SessionDep,
    service: ServiceDep,
    run: bool = Query(default=True),
) -> dict[str, object]:
    task, created = await service.create_task(
        session, request.config, idempotency_key=request.idempotency_key
    )
    if run and task.status not in {"succeeded", "cancelled"}:
        try:
            task = await service.run_task(session, task.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = task_to_dict(task)
    payload["created"] = created
    return payload


@router.get("")
async def backtests(
    session: SessionDep, limit: int = Query(default=30, ge=1, le=200)
) -> list[dict[str, object]]:
    return [task_to_dict(task) for task in await list_tasks(session, limit)]


@router.get("/results/{result_id}")
async def backtest_result(result_id: int, session: SessionDep) -> dict[str, object]:
    detail = await result_detail(session, result_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="backtest result not found")
    return detail


@router.get("/results/{result_id}/artifacts/{kind}", response_class=FileResponse)
async def backtest_artifact(
    result_id: int,
    kind: Literal[
        "json",
        "html",
        "pdf",
        "svg",
        "equity_csv",
        "orders_csv",
        "trades_csv",
        "positions_csv",
        "fees_csv",
    ],
    session: SessionDep,
) -> FileResponse:
    result = await session.get(BacktestResultRecord, result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="backtest result not found")
    paths = {
        "json": result.report_json,
        "html": result.report_html,
        "pdf": result.report_pdf,
        "svg": result.report_svg,
        "equity_csv": None,
        "orders_csv": None,
        "trades_csv": None,
        "positions_csv": None,
        "fees_csv": None,
    }
    if result.report_json:
        prefix = Path(result.report_json).with_suffix("")
        for csv_kind in (
            "equity_csv",
            "orders_csv",
            "trades_csv",
            "positions_csv",
            "fees_csv",
        ):
            suffix = csv_kind.removesuffix("_csv")
            paths[csv_kind] = str(prefix.parent / f"{prefix.name}_{suffix}.csv")
    stored = paths[kind]
    if not stored:
        raise HTTPException(status_code=404, detail="report artifact not available")
    report_root = Path("reports/backtests").resolve()
    path = Path(stored).resolve()
    if not path.is_relative_to(report_root) or not path.is_file():
        raise HTTPException(status_code=404, detail="report artifact not available")
    return FileResponse(path, filename=path.name)


@router.get("/{task_id}")
async def backtest(task_id: int, session: SessionDep) -> dict[str, object]:
    task = await get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="backtest task not found")
    return task_to_dict(task)


@router.post("/{task_id}/cancel")
async def cancel_backtest(
    task_id: int, session: SessionDep, service: ServiceDep
) -> dict[str, object]:
    try:
        return task_to_dict(await service.cancel_task(session, task_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="backtest task not found") from exc
