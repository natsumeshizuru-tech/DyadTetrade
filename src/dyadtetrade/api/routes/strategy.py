from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dyadtetrade.modules.strategy import strategy_registry

router = APIRouter(prefix="/strategies", tags=["strategies"])


class StrategyEnabledRequest(BaseModel):
    enabled: bool


@router.get("")
def strategies() -> list[dict[str, object]]:
    return [
        {
            "name": item.name,
            "version": item.version,
            "description": item.description,
            "parameters_schema": item.parameters_schema,
            "enabled": item.enabled,
        }
        for item in strategy_registry.list_strategies()
    ]


@router.put("/{name}/enabled")
def set_strategy_enabled(name: str, request: StrategyEnabledRequest) -> dict[str, object]:
    try:
        strategy_registry.set_enabled(name, request.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="strategy not found") from exc
    return {"name": name, "enabled": request.enabled}
