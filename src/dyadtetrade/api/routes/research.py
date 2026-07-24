from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.models import FactorRunRecord
from dyadtetrade.db.session import get_session
from dyadtetrade.modules.research import (
    BUILTIN_FACTOR_REGISTRY,
    FactorResearchService,
    FactorRunConfig,
    analyze_factor,
)

router = APIRouter(prefix="/research", tags=["research"])
factor_service = FactorResearchService()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class FactorAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[dict[str, Any]] = Field(max_length=100_000)
    factor_columns: list[str] = Field(min_length=1, max_length=20)
    return_column: str = "forward_return"
    groups: int = Field(default=5, ge=2, le=20)
    max_decay: int = Field(default=5, ge=1, le=20)


class FactorRunRequest(FactorAnalysisRequest):
    definition_name: str = "momentum_5d"
    definition_version: str | None = None
    input_fields: list[str] = Field(default_factory=lambda: ["close"], min_length=1)
    stock_pool: str = "custom"
    parameters: dict[str, Any] = Field(default_factory=dict)
    data_version: str
    code_version: str
    visible_time_field: str = "visible_at"
    idempotency_key: str | None = Field(default=None, max_length=64)


@router.post("/factors/analyze")
def factor_analysis(request: FactorAnalysisRequest) -> dict[str, object]:
    try:
        result = analyze_factor(
            pd.DataFrame(request.records),
            factor_columns=request.factor_columns,
            return_column=request.return_column,
            groups=request.groups,
            max_decay=request.max_decay,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "summary": result.summary,
        "daily_ic": result.daily_ic.to_dict(orient="records"),
        "group_returns": result.group_returns.to_dict(orient="records"),
        "long_short": result.long_short.to_dict(orient="records"),
        "decay": result.decay.to_dict(orient="records"),
        "turnover": result.turnover.to_dict(orient="records"),
        "correlation": result.correlation.to_dict(),
    }


@router.get("/factors/definitions")
async def factor_definitions(session: SessionDep) -> list[dict[str, Any]]:
    await factor_service.sync_definitions(session)
    return [definition.model_dump() for definition in BUILTIN_FACTOR_REGISTRY.list()]


@router.post("/factors/runs", status_code=201)
async def create_factor_run(request: FactorRunRequest, session: SessionDep) -> dict[str, Any]:
    config = FactorRunConfig(
        definition_name=request.definition_name,
        definition_version=request.definition_version,
        factor_columns=request.factor_columns,
        input_fields=request.input_fields,
        return_column=request.return_column,
        groups=request.groups,
        max_decay=request.max_decay,
        stock_pool=request.stock_pool,
        parameters=request.parameters,
        data_version=request.data_version,
        code_version=request.code_version,
        visible_time_field=request.visible_time_field,
    )
    try:
        output = await factor_service.create_and_run(
            session,
            config,
            pd.DataFrame(request.records),
            idempotency_key=request.idempotency_key,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "run": factor_run_to_dict(output.record),
        "result": output.result,
        "created": output.created,
    }


@router.get("/factors/runs")
async def list_factor_runs(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(FactorRunRecord).order_by(FactorRunRecord.id.desc()).offset(offset).limit(limit)
        )
    ).all()
    return {"records": [factor_run_to_dict(row) for row in rows], "count": len(rows)}


@router.get("/factors/runs/{run_id}")
async def get_factor_run(run_id: int, session: SessionDep) -> dict[str, Any]:
    row = await session.get(FactorRunRecord, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="factor run not found")
    result: dict[str, Any] | None = None
    if row.result_path and Path(row.result_path).exists():
        result = dict(json.loads(Path(row.result_path).read_text(encoding="utf-8")))
    return {"run": factor_run_to_dict(row), "result": result}


def factor_run_to_dict(row: FactorRunRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "progress": row.progress,
        "stock_pool": row.stock_pool,
        "input_fields": json.loads(row.input_fields_json),
        "parameters": json.loads(row.parameters_json),
        "data_version": row.data_version,
        "code_version": row.code_version,
        "visible_time_field": row.visible_time_field,
        "result_path": row.result_path,
        "error_message": row.error_message,
        "calculated_at": row.calculated_at.isoformat() if row.calculated_at else None,
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }
