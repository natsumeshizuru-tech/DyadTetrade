from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from dyadtetrade.api.routes.execution import router as execution_router
from dyadtetrade.api.routes.monitoring import router as monitoring_router
from dyadtetrade.api.routes.paper_trading import handle_realtime_batch, paper_runners, paper_service
from dyadtetrade.api.routes.paper_trading import router as paper_trading_router
from dyadtetrade.api.routes.realtime import realtime_service
from dyadtetrade.api.routes.realtime import router as realtime_router
from dyadtetrade.api.routes.strategy import router as strategy_router
from dyadtetrade.api.routes.strategy_bundles import router as strategy_bundles_router
from dyadtetrade.api.routes.system import router as system_router
from dyadtetrade.api.routes.xtdata import router as xtdata_router
from dyadtetrade.core.config import get_settings
from dyadtetrade.core.logging import configure_logging, request_id_context
from dyadtetrade.core.observability import HTTP_LATENCY, HTTP_REQUESTS
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.domain import Bar, Tick
from dyadtetrade.modules.paper_trading.repository import (
    recover_paper_runners,
    recover_paper_state,
)
from dyadtetrade.modules.realtime import xtdata_subscription_service


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Trading execution, paper simulation, OMS, risk, realtime market data, "
            "account reconciliation, strategy runtime monitoring, and the controlled local "
            "XtData download/import plane. Historical queries, factor research, and "
            "backtesting belong to DyadResearch."
        ),
        lifespan=lifespan,
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
            elapsed = perf_counter() - started
            route = request.url.path
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)
            request_id_context.reset(token)

    app.include_router(system_router, prefix=settings.api_v1_prefix)
    app.include_router(monitoring_router, prefix=settings.api_v1_prefix)
    app.include_router(strategy_router, prefix=settings.api_v1_prefix)
    app.include_router(strategy_bundles_router, prefix=settings.api_v1_prefix)
    app.include_router(execution_router, prefix=settings.api_v1_prefix)
    app.include_router(realtime_router, prefix=settings.api_v1_prefix)
    app.include_router(paper_trading_router, prefix=settings.api_v1_prefix)
    app.include_router(xtdata_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
