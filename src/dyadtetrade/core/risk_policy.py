from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dyadtetrade.core.config import Settings


class ExecutionRiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_order_notional: Decimal = Field(gt=0)
    max_daily_notional: Decimal = Field(gt=0)
    max_orders_per_minute: int = Field(gt=0, le=10_000)
    max_symbol_position_notional: Decimal = Field(gt=0)
    max_total_position_notional: Decimal = Field(gt=0)
    max_total_position_ratio: Decimal = Field(gt=0, le=1)
    max_daily_orders: int = Field(gt=0, le=1_000_000)
    max_strategy_daily_notional: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_relationships(self) -> ExecutionRiskPolicy:
        if self.max_order_notional > self.max_daily_notional:
            raise ValueError("单笔名义金额不能超过单日名义金额")
        if self.max_symbol_position_notional > self.max_total_position_notional:
            raise ValueError("单标的持仓上限不能超过总持仓上限")
        if self.max_strategy_daily_notional > self.max_daily_notional:
            raise ValueError("单策略日限额不能超过账户单日限额")
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> ExecutionRiskPolicy:
        return cls(
            max_order_notional=settings.max_order_notional,
            max_daily_notional=settings.max_daily_notional,
            max_orders_per_minute=settings.max_orders_per_minute,
            max_symbol_position_notional=settings.max_symbol_position_notional,
            max_total_position_notional=settings.max_total_position_notional,
            max_total_position_ratio=settings.max_total_position_ratio,
            max_daily_orders=settings.max_daily_orders,
            max_strategy_daily_notional=settings.max_strategy_daily_notional,
        )

    def ensure_within(self, ceiling: ExecutionRiskPolicy) -> None:
        for name in type(self).model_fields:
            if getattr(self, name) > getattr(ceiling, name):
                raise ValueError(f"{name} cannot exceed the startup safety ceiling")

    def apply(self, settings: Settings) -> None:
        for name in type(self).model_fields:
            setattr(settings, name, getattr(self, name))

    def api_dict(self) -> dict[str, str | int]:
        result: dict[str, str | int] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            result[name] = str(value) if isinstance(value, Decimal) else value
        return result


def risk_policy_path(settings: Settings) -> Path:
    return settings.data_root / "execution_risk_policy.json"


def load_risk_policy(path: Path) -> ExecutionRiskPolicy | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("policy"), dict):
        raise ValueError("persisted execution risk policy is invalid")
    return ExecutionRiskPolicy.model_validate(payload["policy"])


def save_risk_policy(path: Path, policy: ExecutionRiskPolicy, *, actor: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "policy": policy.api_dict(),
        "actor": actor,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def risk_policy_snapshot(
    current: ExecutionRiskPolicy,
    ceiling: ExecutionRiskPolicy,
    *,
    editable: bool,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "current": current.api_dict(),
        "ceiling": ceiling.api_dict(),
        "editable": editable,
        "requires_restricted_state": True,
        "error": error,
    }
