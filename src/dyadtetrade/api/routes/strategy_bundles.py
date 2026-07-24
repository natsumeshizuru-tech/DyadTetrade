from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dyadtetrade.contracts import BundleImportPolicy, validate_release_bundle
from dyadtetrade.core.config import ExecutionMode, get_settings
from dyadtetrade.db.session import SessionFactory
from dyadtetrade.modules.execution import StrategyExecutionReleaseService

router = APIRouter(prefix="/strategy-bundles", tags=["strategy-bundles"])


class BundleValidationRequest(BaseModel):
    bundle_name: str = Field(min_length=1, max_length=128)
    execution_target: Literal["paper", "qmt_sim", "qmt_live"] = "paper"
    actor: str = Field(default="operator", min_length=2, max_length=128)


class BundleImportRequest(BaseModel):
    bundle_name: str = Field(min_length=1, max_length=128)
    execution_target: Literal["qmt_sim", "qmt_live"] = "qmt_sim"
    universe_release_id: str = Field(min_length=1, max_length=64)
    actor: str = Field(default="operator", min_length=2, max_length=128)


def _bundle_path(name: str) -> Path:
    if Path(name).name != name or name in {".", ".."}:
        raise ValueError("bundle_name must be a plain directory name")
    root = get_settings().strategy_bundle_inbox.resolve()
    candidate = (root / name).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_dir():
        raise ValueError("bundle is outside the configured inbox")
    return candidate


def _policy() -> BundleImportPolicy:
    settings = get_settings()
    return BundleImportPolicy(
        allowed_strategy_ids=frozenset(settings.allowed_strategy_bundle_ids),
        runtime_api_version=settings.strategy_runtime_api_version,
        data_capabilities=frozenset(settings.strategy_data_capabilities),
        maximum_risk_budget={
            "max_position_weight": float(settings.max_total_position_ratio),
            "max_gross_exposure": float(settings.max_total_position_ratio),
            "max_daily_turnover": 10.0,
            "max_order_notional": float(settings.max_order_notional),
        },
    )


@router.get("")
def list_strategy_bundles() -> dict[str, object]:
    root = get_settings().strategy_bundle_inbox
    root.mkdir(parents=True, exist_ok=True)
    bundles: list[dict[str, object]] = []
    for path in sorted(item for item in root.iterdir() if item.is_dir() and not item.is_symlink()):
        manifest_path = path / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            bundles.append(
                {
                    "name": path.name,
                    "strategy_id": manifest.get("strategy_id"),
                    "strategy_version": manifest.get("strategy_version"),
                    "approval_status": manifest.get("approval_status"),
                    "readable": True,
                }
            )
        except (OSError, json.JSONDecodeError):
            bundles.append({"name": path.name, "readable": False})
    return {"inbox": str(root.resolve()), "bundles": bundles}


def _validate_and_receipt(
    *, bundle_name: str, execution_target: Literal["paper", "qmt_sim", "qmt_live"], actor: str
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        bundle_path = _bundle_path(bundle_name)
        result = validate_release_bundle(
            bundle_path,
            _policy(),
            execution_target=execution_target,
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manifest: dict[str, object] = json.loads(
        (bundle_path / "manifest.json").read_text(encoding="utf-8")
    )
    receipt = {
        **asdict(result),
        "bundle_path": str(result.bundle_path),
        "actor": actor,
        "receipt_created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    receipts = get_settings().strategy_bundle_inbox.parent / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    destination = receipts / f"{result.bundle_hash}-{execution_target}.json"
    temporary = destination.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    receipt["receipt_path"] = str(destination.resolve())
    return receipt, manifest


@router.post("/validate")
def validate_strategy_bundle(request: BundleValidationRequest) -> dict[str, object]:
    receipt, _ = _validate_and_receipt(
        bundle_name=request.bundle_name,
        execution_target=request.execution_target,
        actor=request.actor,
    )
    return receipt


@router.post("/import")
async def import_strategy_bundle(request: BundleImportRequest) -> dict[str, object]:
    """Validate a research bundle and register a draft; activation remains manual."""

    receipt, manifest = _validate_and_receipt(
        bundle_name=request.bundle_name,
        execution_target=request.execution_target,
        actor=request.actor,
    )
    settings = get_settings()
    risk_budget = manifest.get("default_risk_budget")
    parameters = manifest.get("parameters")
    if not isinstance(risk_budget, dict) or not isinstance(parameters, dict):
        raise HTTPException(status_code=422, detail="bundle risk budget or parameters are invalid")
    order_limit = Decimal(str(risk_budget["max_order_notional"]))
    daily_limit = min(settings.max_strategy_daily_notional, settings.max_daily_notional)
    if order_limit > daily_limit:
        raise HTTPException(
            status_code=422,
            detail="bundle order limit exceeds the trading-side strategy daily limit",
        )
    try:
        async with SessionFactory() as session:
            execution_release = await StrategyExecutionReleaseService.create(
                session,
                execution_mode=ExecutionMode(request.execution_target),
                strategy_id=str(manifest["strategy_id"]),
                strategy_name=str(manifest["strategy_id"]),
                strategy_version=str(manifest["strategy_version"]),
                universe_release_id=request.universe_release_id,
                parameters={str(key): value for key, value in parameters.items()},
                max_order_notional=order_limit,
                max_daily_notional=daily_limit,
            )
    except (KeyError, PermissionError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    receipt["execution_release"] = execution_release
    receipt_path = Path(str(receipt["receipt_path"]))
    temporary = receipt_path.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, receipt_path)
    return receipt
