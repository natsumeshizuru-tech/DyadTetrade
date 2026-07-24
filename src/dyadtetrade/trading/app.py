from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from dyadtetrade.core.config import get_settings
from dyadtetrade.core.risk_policy import ExecutionRiskPolicy
from dyadtetrade.domain import RiskOperatingState
from dyadtetrade.trading.engine import TradingEngineService

service = TradingEngineService()


class RiskTransitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: RiskOperatingState
    reason: str
    actor: str = "operator"


class RiskPolicyInput(ExecutionRiskPolicy):
    actor: str = Field(default="operator", min_length=2, max_length=128)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: str
    quantity: int
    limit_price: str
    strategy_id: str
    client_order_id: str = Field(min_length=1, max_length=48)
    idempotency_key: str = Field(min_length=1, max_length=64)
    confirm_simulated_order: bool = False
    confirm_live_order: bool = False
    live_confirmation: str = ""
    automated: bool = False
    strategy_release_id: str = ""
    strategy_version: str = ""
    parameters_hash: str = ""
    signal_id: str = ""


def authorize(x_trading_engine_token: str = Header(default="")) -> None:
    expected = get_settings().trading_engine_token
    if not expected or x_trading_engine_token != expected:
        raise HTTPException(status_code=403, detail="invalid trading engine token")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await service.start()
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(title="DyadTetrade Trading Engine", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "role": "trading-engine"}


@app.get("/internal/v1/status", dependencies=[Depends(authorize)])
def status() -> dict[str, object]:
    return service.status()


@app.post("/internal/v1/reconcile", dependencies=[Depends(authorize)])
async def reconcile() -> dict[str, object]:
    return await service.reconcile()


@app.put("/internal/v1/risk-state", dependencies=[Depends(authorize)])
async def transition_risk(request: RiskTransitionInput) -> dict[str, object]:
    try:
        return await service.transition_risk(request.state, request.reason, request.actor)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/internal/v1/risk-policy", dependencies=[Depends(authorize)])
def risk_policy() -> dict[str, object]:
    value = service.status().get("risk_policy")
    return dict(value) if isinstance(value, dict) else {}


@app.put("/internal/v1/risk-policy", dependencies=[Depends(authorize)])
async def update_risk_policy(request: RiskPolicyInput) -> dict[str, object]:
    try:
        policy = ExecutionRiskPolicy.model_validate(request.model_dump(exclude={"actor"}))
        return await service.update_risk_policy(policy, request.actor)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/internal/v1/orders", dependencies=[Depends(authorize)])
async def order(request: OrderInput) -> dict[str, object]:
    try:
        return await service.submit_order(request.model_dump())
    except (PermissionError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/internal/v1/orders/{order_id}/cancel", dependencies=[Depends(authorize)])
async def cancel(order_id: int, confirmed: bool) -> dict[str, object]:
    try:
        return await service.cancel_order(order_id, confirmed)
    except (PermissionError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/internal/v1/release-gate", dependencies=[Depends(authorize)])
def release_gate() -> dict[str, object]:
    return service.release_gate()
