from typing import Annotated

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade import __version__
from dyadtetrade.api.routes.paper_trading import paper_runners, paper_service
from dyadtetrade.api.routes.realtime import realtime_service
from dyadtetrade.core.config import get_settings
from dyadtetrade.core.observability import (
    PAPER_RECONCILED,
    QMT_ACCOUNT_CONNECTED,
    REALTIME_QUOTE_AGE,
    REALTIME_WATCHLIST,
    RUNNING_STRATEGIES,
)
from dyadtetrade.db.session import get_session
from dyadtetrade.integrations.xtquant import get_xtquant_status
from dyadtetrade.modules.registry import MODULES, ModuleInfo
from dyadtetrade.trading.client import trading_engine_client

router = APIRouter(tags=["system"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "DyadTetrade", "version": __version__}


@router.get("/modules", response_model=list[ModuleInfo])
def modules() -> list[ModuleInfo]:
    return MODULES


@router.get("/integrations/xtquant")
def xtquant_status() -> dict[str, object]:
    status = get_xtquant_status()
    return {
        "installed": status.installed,
        "path": status.path,
        "connected": status.connected,
        "sector_count": status.sector_count,
        "live_trading_enabled": status.live_trading_enabled,
        "error": status.error,
    }


@router.get("/ready")
async def readiness(session: SessionDep) -> dict[str, object]:
    await session.execute(text("SELECT 1"))
    settings = get_settings()
    market = get_xtquant_status()
    PAPER_RECONCILED.set(1 if paper_service.last_reconciled_at else 0)
    realtime = realtime_service.snapshot()
    watchlist_count = realtime.get("watchlist_count")
    REALTIME_WATCHLIST.set(
        float(watchlist_count) if isinstance(watchlist_count, (int, float)) else 0
    )
    quote_age = realtime.get("quote_age_seconds")
    REALTIME_QUOTE_AGE.set(float(quote_age) if isinstance(quote_age, (int, float)) else -1)
    RUNNING_STRATEGIES.set(sum(item.running for item in paper_runners.values()))
    execution = trading_engine_client.status()
    QMT_ACCOUNT_CONNECTED.set(
        1 if execution.get("connected") and execution.get("subscribed") else 0
    )
    return {
        "status": "ready",
        "database": "ok",
        "live_trading_enabled": settings.enable_live_trading,
        "miniqmt": "connected" if market.connected else "optional_unavailable",
        "historical_data_owner": "DyadResearch",
        "paper_reconciled_at": (
            paper_service.last_reconciled_at.isoformat()
            if paper_service.last_reconciled_at
            else None
        ),
        "execution": execution,
        "realtime": realtime,
    }


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
