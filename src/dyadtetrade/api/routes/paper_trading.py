from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.session import SessionFactory, get_session
from dyadtetrade.domain import Bar, Direction, Interval, OrderRequest, Tick
from dyadtetrade.modules.paper_trading import PaperStrategyRunner, PaperTradingService
from dyadtetrade.modules.paper_trading.repository import save_paper_runners, save_paper_state

router = APIRouter(prefix="/paper", tags=["paper-trading"])
paper_service = PaperTradingService()
paper_runners: dict[str, PaperStrategyRunner] = {}
_realtime_lock = asyncio.Lock()
_last_realtime_persist: datetime | None = None
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class PaperBarInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    timestamp: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: int = Field(ge=0)
    suspended: bool = False
    limit_up: Decimal | None = None
    limit_down: Decimal | None = None


class PaperOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: Direction
    quantity: int = Field(gt=0)
    strategy_id: str = "manual-paper"
    idempotency_key: str


class PaperStrategyStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_name: str
    strategy_id: str | None = None
    parameters: dict[str, object] = Field(default_factory=dict)


@router.get("/snapshot")
def paper_snapshot() -> dict[str, object]:
    return paper_service.snapshot()


@router.post("/market/bars")
async def paper_market_bars(bars: list[PaperBarInput], session: SessionDep) -> dict[str, object]:
    if not bars or len(bars) > 1000:
        raise HTTPException(status_code=422, detail="provide 1 to 1000 bars")
    timestamps = {item.timestamp for item in bars}
    if len(timestamps) != 1:
        raise HTTPException(status_code=422, detail="all bars in a batch need one timestamp")
    converted = [
        Bar(
            item.symbol,
            item.timestamp,
            Interval.MINUTE_1,
            item.open,
            item.high,
            item.low,
            item.close,
            item.volume,
            suspended=item.suspended,
            limit_up=item.limit_up,
            limit_down=item.limit_down,
            source="paper-input",
        )
        for item in bars
    ]
    for runner in tuple(paper_runners.values()):
        if runner.running:
            paper_service.heartbeat_strategy(runner.strategy.strategy_id, bars[0].timestamp)
    paper_service.on_bars(bars[0].timestamp, converted)
    for runner in tuple(paper_runners.values()):
        runner.after_market_bars(converted)
    await save_paper_state(session, paper_service)
    await save_paper_runners(session, paper_runners)
    return paper_service.snapshot()


@router.post("/orders", status_code=201)
async def paper_order(request: PaperOrderInput, session: SessionDep) -> dict[str, object]:
    order = paper_service.submit(
        OrderRequest(
            request.symbol,
            request.direction,
            request.quantity,
            strategy_id=request.strategy_id,
            account_id="paper",
            idempotency_key=request.idempotency_key,
        )
    )
    await save_paper_state(session, paper_service)
    return {
        "order_id": order.order_id,
        "status": order.status.value,
        "reject_reason": order.reject_reason,
        "live_order": False,
    }


@router.post("/orders/{order_id}/cancel")
async def paper_cancel(order_id: str, session: SessionDep) -> dict[str, object]:
    order = paper_service.cancel(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="paper order not found")
    await save_paper_state(session, paper_service)
    return {"order_id": order.order_id, "status": order.status.value, "live_order": False}


@router.get("/strategies")
def paper_strategies() -> list[dict[str, object]]:
    return [runner.status() for runner in paper_runners.values()]


async def handle_realtime_batch(ticks: list[Tick], bars: list[Bar]) -> None:
    """Feed one bounded XtData batch into every running paper strategy."""
    global _last_realtime_persist
    running = [runner for runner in paper_runners.values() if runner.running]
    if not running or not ticks or not bars:
        return
    async with _realtime_lock:
        timestamp = max(item.timestamp for item in ticks)
        for runner in running:
            paper_service.heartbeat_strategy(runner.strategy.strategy_id, timestamp)
        paper_service.on_bars(timestamp, bars)
        for runner in running:
            runner.after_market_ticks(ticks, bars)
        now = datetime.now(UTC)
        if _last_realtime_persist is None or (now - _last_realtime_persist).total_seconds() >= 10:
            async with SessionFactory() as session:
                await save_paper_state(session, paper_service)
                await save_paper_runners(session, paper_runners)
            _last_realtime_persist = now


@router.post("/strategies/start", status_code=201)
async def start_paper_strategy(
    request: PaperStrategyStartInput, session: SessionDep
) -> dict[str, object]:
    strategy_id = request.strategy_id or f"paper-{request.strategy_name}-{uuid4().hex[:8]}"
    existing = paper_runners.get(strategy_id)
    if existing is not None:
        existing.start()
        await save_paper_runners(session, paper_runners)
        return existing.status()
    try:
        runner = PaperStrategyRunner(
            paper_service,
            request.strategy_name,
            strategy_id,
            request.parameters,
        )
        runner.start()
    except (KeyError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    paper_runners[strategy_id] = runner
    await save_paper_runners(session, paper_runners)
    return runner.status()


@router.post("/strategies/{strategy_id}/stop")
async def stop_paper_strategy(strategy_id: str, session: SessionDep) -> dict[str, object]:
    runner = paper_runners.get(strategy_id)
    if runner is None:
        raise HTTPException(status_code=404, detail="paper strategy not found")
    runner.stop()
    await save_paper_runners(session, paper_runners)
    return runner.status()
