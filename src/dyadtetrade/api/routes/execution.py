from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dyadtetrade.core.config import ExecutionMode, get_settings
from dyadtetrade.core.risk_policy import ExecutionRiskPolicy, risk_policy_snapshot
from dyadtetrade.db.session import SessionFactory
from dyadtetrade.domain import Direction, RiskOperatingState
from dyadtetrade.modules.execution import (
    ExecutionUniverseFilters,
    ExecutionUniverseService,
    StrategyExecutionReleaseService,
)
from dyadtetrade.trading.client import trading_engine_client

router = APIRouter(prefix="/execution", tags=["execution"])


class ManualQmtOrder(BaseModel):
    symbol: str
    direction: Direction
    quantity: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    strategy_id: str = "manual"
    client_order_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
        max_length=48,
    )
    idempotency_key: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
        max_length=64,
    )
    confirm_simulated_order: bool = False
    confirm_live_order: bool = False
    live_confirmation: str = ""


class RiskStateUpdate(BaseModel):
    state: RiskOperatingState
    reason: str = Field(min_length=3, max_length=1024)
    actor: str = "operator"


class SwitchUpdate(BaseModel):
    kill_switch: bool | None = None
    cancel_only: bool | None = None
    no_new_positions: bool | None = None


class RiskPolicyUpdate(ExecutionRiskPolicy):
    actor: str = Field(default="frontend-operator", min_length=2, max_length=128)


class ExecutionUniverseBuild(BaseModel):
    execution_mode: ExecutionMode = ExecutionMode.QMT_SIM
    asset_types: list[str] = Field(default_factory=lambda: ["stock", "etf"])
    exchanges: list[str] = Field(default_factory=lambda: ["SH", "SZ", "BJ"])
    exclude_st: bool = True
    exclude_delisting: bool = True
    minimum_listed_days: int = Field(default=20, ge=0, le=3650)
    require_daily_data: bool = True


class ReleaseAction(BaseModel):
    actor: str = Field(min_length=2, max_length=128)
    confirmation: str = ""


class StrategyReleaseCreate(BaseModel):
    execution_mode: ExecutionMode = ExecutionMode.QMT_SIM
    strategy_id: str = Field(min_length=1, max_length=128)
    strategy_name: str = Field(min_length=1, max_length=128)
    strategy_version: str = Field(min_length=1, max_length=64)
    universe_release_id: str = Field(min_length=1, max_length=64)
    parameters: dict[str, object] = Field(default_factory=dict)
    max_order_notional: Decimal = Field(gt=0)
    max_daily_notional: Decimal = Field(gt=0)


class AutomatedQmtOrder(BaseModel):
    symbol: str
    direction: Direction
    quantity: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    strategy_id: str
    strategy_version: str
    strategy_release_id: str
    signal_id: str = Field(min_length=1, max_length=128)
    parameters: dict[str, object] = Field(default_factory=dict)


def _unavailable(exc: RuntimeError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/status")
def execution_status() -> dict[str, object]:
    return trading_engine_client.status()


@router.post("/connect")
async def connect_execution() -> dict[str, object]:
    try:
        return await trading_engine_client.request("POST", "/reconcile")
    except RuntimeError as exc:
        raise _unavailable(exc) from exc


@router.post("/reconcile")
async def reconcile_execution() -> dict[str, object]:
    try:
        return await trading_engine_client.request("POST", "/reconcile")
    except RuntimeError as exc:
        raise _unavailable(exc) from exc


@router.post("/orders")
async def submit_qmt_order(request: ManualQmtOrder) -> dict[str, object]:
    try:
        payload = request.model_dump(mode="json")
        payload["limit_price"] = str(request.limit_price)
        return await trading_engine_client.request("POST", "/orders", json=payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/automated-orders")
async def submit_automated_qmt_order(request: AutomatedQmtOrder) -> dict[str, object]:
    parameter_hash = hashlib.sha256(
        json.dumps(request.parameters, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    idempotency_key = hashlib.sha256(
        f"{request.strategy_release_id}:{request.signal_id}".encode()
    ).hexdigest()
    payload = request.model_dump(mode="json")
    payload.update(
        {
            "limit_price": str(request.limit_price),
            "client_order_id": f"auto-{idempotency_key[:27]}",
            "idempotency_key": idempotency_key,
            "parameters_hash": parameter_hash,
            "automated": True,
        }
    )
    try:
        return await trading_engine_client.request("POST", "/orders", json=payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/universe/releases")
async def build_execution_universe(request: ExecutionUniverseBuild) -> dict[str, object]:
    try:
        filters = ExecutionUniverseFilters(
            asset_types=tuple(request.asset_types),
            exchanges=tuple(request.exchanges),
            exclude_st=request.exclude_st,
            exclude_delisting=request.exclude_delisting,
            minimum_listed_days=request.minimum_listed_days,
            require_daily_data=request.require_daily_data,
        )
        async with SessionFactory() as session:
            return await ExecutionUniverseService.build(
                session,
                execution_mode=request.execution_mode,
                filters=filters,
            )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/universe/releases/latest")
async def latest_execution_universe() -> dict[str, object] | None:
    async with SessionFactory() as session:
        result = await ExecutionUniverseService.latest(session)
    return result


@router.post("/universe/releases/{release_id}/review")
async def review_execution_universe(release_id: str, request: ReleaseAction) -> dict[str, object]:
    try:
        async with SessionFactory() as session:
            return await ExecutionUniverseService.review(
                session, release_id, reviewer=request.actor
            )
    except (KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/universe/releases/{release_id}/activate")
async def activate_execution_universe(release_id: str, request: ReleaseAction) -> dict[str, object]:
    try:
        async with SessionFactory() as session:
            return await ExecutionUniverseService.activate(
                session,
                release_id,
                approver=request.actor,
                confirmation=request.confirmation,
            )
    except (KeyError, PermissionError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/universe/releases/{release_id}/revoke")
async def revoke_execution_universe(release_id: str, request: ReleaseAction) -> dict[str, object]:
    try:
        async with SessionFactory() as session:
            return await ExecutionUniverseService.revoke(session, release_id, actor=request.actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/strategy-releases")
async def create_strategy_execution_release(
    request: StrategyReleaseCreate,
) -> dict[str, object]:
    try:
        async with SessionFactory() as session:
            return await StrategyExecutionReleaseService.create(
                session,
                execution_mode=request.execution_mode,
                strategy_id=request.strategy_id,
                strategy_name=request.strategy_name,
                strategy_version=request.strategy_version,
                universe_release_id=request.universe_release_id,
                parameters=request.parameters,
                max_order_notional=request.max_order_notional,
                max_daily_notional=request.max_daily_notional,
            )
    except (KeyError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/strategy-releases")
async def list_strategy_execution_releases() -> list[dict[str, object]]:
    async with SessionFactory() as session:
        return await StrategyExecutionReleaseService.list(session)


@router.post("/strategy-releases/{release_id}/activate")
async def activate_strategy_execution_release(
    release_id: str, request: ReleaseAction
) -> dict[str, object]:
    try:
        async with SessionFactory() as session:
            return await StrategyExecutionReleaseService.activate(
                session,
                release_id,
                approver=request.actor,
                confirmation=request.confirmation,
            )
    except (KeyError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/strategy-releases/{release_id}/revoke")
async def revoke_strategy_execution_release(
    release_id: str, request: ReleaseAction
) -> dict[str, object]:
    try:
        async with SessionFactory() as session:
            return await StrategyExecutionReleaseService.revoke(
                session, release_id, actor=request.actor
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/orders/{qmt_order_id}/cancel")
async def cancel_qmt_order(
    qmt_order_id: int,
    confirm_simulated_order: bool = False,
    confirm_live_cancel: bool = False,
) -> dict[str, object]:
    try:
        return await trading_engine_client.request(
            "POST",
            f"/orders/{qmt_order_id}/cancel",
            params={"confirmed": confirm_simulated_order or confirm_live_cancel},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/risk-state")
def risk_state() -> dict[str, object]:
    status = trading_engine_client.status()
    value = status.get("risk_state")
    return dict(value) if isinstance(value, dict) else {"state": "RECOVERY"}


@router.put("/risk-state")
async def update_risk_state(request: RiskStateUpdate) -> dict[str, object]:
    try:
        return await trading_engine_client.request(
            "PUT", "/risk-state", json=request.model_dump(mode="json")
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/risk-policy")
async def execution_risk_policy() -> dict[str, object]:
    try:
        return await trading_engine_client.request("GET", "/risk-policy")
    except RuntimeError as exc:
        ceiling = ExecutionRiskPolicy.from_settings(get_settings())
        return risk_policy_snapshot(ceiling, ceiling, editable=False, error=str(exc))


@router.put("/risk-policy")
async def update_execution_risk_policy(request: RiskPolicyUpdate) -> dict[str, object]:
    try:
        return await trading_engine_client.request(
            "PUT", "/risk-policy", json=request.model_dump(mode="json")
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/switches")
async def update_switches(request: SwitchUpdate) -> dict[str, object]:
    current = risk_state().get("state", "RECOVERY")
    target = current
    reason = "compatibility switch update"
    if request.kill_switch is True:
        target = RiskOperatingState.HALTED.value
        reason = "Kill Switch activated"
    elif request.kill_switch is False and current == RiskOperatingState.HALTED.value:
        target = RiskOperatingState.RECOVERY.value
        reason = "Kill Switch released into recovery"
    elif request.cancel_only is True:
        target = RiskOperatingState.CANCEL_ONLY.value
    elif request.no_new_positions is True:
        target = RiskOperatingState.REDUCE_ONLY.value
    try:
        return await trading_engine_client.request(
            "PUT",
            "/risk-state",
            json={"state": target, "reason": reason, "actor": "compatibility-api"},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/release-gate")
async def release_gate() -> dict[str, object]:
    try:
        return await trading_engine_client.request("GET", "/release-gate")
    except RuntimeError as exc:
        raise _unavailable(exc) from exc
