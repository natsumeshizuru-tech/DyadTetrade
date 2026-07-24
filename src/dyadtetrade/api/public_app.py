from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from dyadtetrade import __version__
from dyadtetrade.api.routes.execution import router as execution_router
from dyadtetrade.api.routes.paper_trading import handle_realtime_batch, paper_runners, paper_service
from dyadtetrade.api.routes.paper_trading import router as paper_trading_router
from dyadtetrade.api.routes.realtime import realtime_service
from dyadtetrade.api.routes.realtime import router as realtime_router
from dyadtetrade.api.routes.strategy import router as strategy_router
from dyadtetrade.core.config import ExecutionMode, get_settings
from dyadtetrade.core.logging import configure_logging, request_id_context
from dyadtetrade.core.observability import HTTP_LATENCY, HTTP_REQUESTS
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.domain import Bar, Tick
from dyadtetrade.modules.paper_trading.repository import (
    recover_paper_runners,
    recover_paper_state,
)
from dyadtetrade.modules.realtime import xtdata_subscription_service

public_system_router = APIRouter(prefix="/system", tags=["system"])


@public_system_router.get("/health")
def public_health() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "edition": "public-trading",
        "version": __version__,
        "execution_mode": settings.execution_mode.value,
        "live_trading_enabled": settings.enable_live_trading,
        "historical_data_owner": "DyadResearch",
        "real_orders_sent": 0,
    }


def _assert_public_execution_policy() -> None:
    settings = get_settings()
    if settings.execution_mode not in {ExecutionMode.DISABLED, ExecutionMode.PAPER}:
        raise RuntimeError("the public app supports only disabled or paper execution")
    if settings.enable_live_trading or settings.enable_qmt_live_trading:
        raise RuntimeError("live trading is forbidden in the public app")


@asynccontextmanager
async def public_lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _assert_public_execution_policy()
    settings.validate_execution_safety()
    settings.ensure_directories()
    configure_logging(settings.log_level)
    await initialize_database()
    async with SessionFactory() as session:
        await recover_paper_state(session, paper_service)
        paper_runners.update(await recover_paper_runners(session, paper_service))

    async def dispatch_realtime(ticks: list[Tick], bars: list[Bar]) -> None:
        await handle_realtime_batch(ticks, bars)

    realtime_service.add_handler(dispatch_realtime)
    await realtime_service.start()
    try:
        yield
    finally:
        await xtdata_subscription_service.stop()
        await realtime_service.stop()


def create_public_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} Public Trading API",
        version=__version__,
        description=(
            "Paper-first trading, OMS, risk and realtime API. Historical research and "
            "backtesting are owned by DyadResearch."
        ),
        lifespan=public_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", uuid4().hex)
        token = request_id_context.set(request_id)
        started = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            HTTP_REQUESTS.labels(request.method, request.url.path, str(status)).inc()
            HTTP_LATENCY.labels(request.method, request.url.path).observe(perf_counter() - started)
            request_id_context.reset(token)

    prefix = settings.api_v1_prefix
    app.include_router(public_system_router, prefix=prefix)
    app.include_router(strategy_router, prefix=prefix)
    app.include_router(paper_trading_router, prefix=prefix)
    app.include_router(execution_router, prefix=prefix)
    app.include_router(realtime_router, prefix=prefix)
    return app


app = create_public_app()
