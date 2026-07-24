from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dyadtetrade.modules.realtime import RealtimeComputationService, xtdata_subscription_service

router = APIRouter(prefix="/realtime", tags=["realtime"])
realtime_service = RealtimeComputationService()


class WatchlistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(max_length=500)


class SingleSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(min_length=1, max_length=50)
    period: str = "tick"


class WholeSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markets: list[str] = Field(default_factory=lambda: ["SH", "SZ"], min_length=1)


@router.get("/status")
def realtime_status() -> dict[str, object]:
    return {
        **realtime_service.snapshot(),
        "xtdata_subscription": xtdata_subscription_service.snapshot(),
    }


@router.get("/subscriptions/status")
def subscription_status() -> dict[str, object]:
    return xtdata_subscription_service.snapshot()


@router.post("/subscriptions/single")
async def subscribe_single(request: SingleSubscriptionRequest) -> dict[str, object]:
    try:
        return await xtdata_subscription_service.subscribe_single(request.symbols, request.period)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/subscriptions/whole")
async def subscribe_whole(request: WholeSubscriptionRequest) -> dict[str, object]:
    try:
        return await xtdata_subscription_service.subscribe_whole(request.markets)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/subscriptions")
async def stop_subscriptions() -> dict[str, object]:
    await xtdata_subscription_service.stop()
    return xtdata_subscription_service.snapshot()


@router.get("/subscriptions/latest")
def subscription_latest(symbols: str = "", limit: int = 200) -> dict[str, object]:
    try:
        requested = [item.strip() for item in symbols.split(",") if item.strip()]
        return xtdata_subscription_service.latest(requested, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/watchlist")
async def update_watchlist(request: WatchlistUpdate) -> dict[str, object]:
    try:
        await realtime_service.set_watchlist(request.symbols)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return realtime_service.snapshot()


@router.post("/mainboard-small-cap/refresh")
async def refresh_mainboard_small_cap(limit: int = 50) -> dict[str, object]:
    try:
        candidates = await realtime_service.refresh_mainboard_candidates(limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"count": len(candidates), "symbols": candidates, "orders_sent": 0}


@router.post("/strategies/tick-momentum-small-cap/start", status_code=201)
async def start_tick_momentum_small_cap() -> dict[str, object]:
    from dyadtetrade.api.routes.paper_trading import paper_runners, paper_service
    from dyadtetrade.db.session import SessionFactory
    from dyadtetrade.modules.paper_trading import PaperStrategyRunner
    from dyadtetrade.modules.paper_trading.repository import save_paper_runners

    candidates = realtime_service.mainboard_candidates
    if len(candidates) < 10:
        candidates = await realtime_service.refresh_mainboard_candidates(50)
    if len(candidates) < 10:
        raise HTTPException(status_code=409, detail="not enough eligible main-board candidates")
    existing = next(
        (
            runner
            for runner in paper_runners.values()
            if runner.strategy.name == "tick_momentum_small_cap" and runner.running
        ),
        None,
    )
    if existing is not None:
        return existing.status()
    strategy_id = "paper-tick-momentum-small-cap"
    runner = PaperStrategyRunner(
        paper_service,
        "tick_momentum_small_cap",
        strategy_id,
        {
            "symbols": candidates,
            "float_shares": {
                symbol: realtime_service.float_shares[symbol] for symbol in candidates
            },
            "small_cap_n": min(20, len(candidates)),
            "top_n": min(5, len(candidates)),
            "rebalance_every_ticks": max(len(candidates) * 60, 3000),
            "max_total_weight": "0.25",
            "max_single_weight": "0.05",
        },
    )
    runner.start()
    paper_runners[strategy_id] = runner
    async with SessionFactory() as session:
        await save_paper_runners(session, paper_runners)
    return runner.status()
