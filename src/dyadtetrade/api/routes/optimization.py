from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.models import OptimizationTaskRecord
from dyadtetrade.db.session import get_session
from dyadtetrade.modules.optimization import (
    BacktestOptimizationConfig,
    BacktestOptimizationService,
    OptimizationEngine,
    optimization_task_to_dict,
)

router = APIRouter(prefix="/optimizations", tags=["optimization"])
optimization_service = BacktestOptimizationService()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class OptimizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameter_space: dict[str, list[float | int]]
    target: dict[str, float]
    method: Literal["grid", "random", "optuna"] = "grid"
    max_trials: int = Field(default=20, ge=1, le=500)
    random_seed: int = 0


class BacktestOptimizationTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: BacktestOptimizationConfig
    idempotency_key: str | None = Field(default=None, max_length=64)
    run: bool = True


@router.post("/run")
def run_optimization(request: OptimizationRequest) -> dict[str, object]:
    def objective(parameters: dict[str, float | int], segment: str) -> float:
        penalty = sum(
            abs(float(value) - float(request.target.get(name, value)))
            for name, value in parameters.items()
        )
        return -penalty * (1.0 if segment == "in_sample" else 1.1)

    try:
        result = OptimizationEngine().run(
            request.parameter_space,
            objective,
            method=request.method,
            max_trials=request.max_trials,
            random_seed=request.random_seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "method": result.method,
        "best": {
            "parameters": result.best.parameters,
            "in_sample_score": result.best.in_sample_score,
            "out_of_sample_score": result.best.out_of_sample_score,
            "robust_score": result.best.robust_score,
        },
        "neighborhood_stability": result.neighborhood_stability,
        "warnings": result.warnings,
        "trials": [
            {
                "number": trial.number,
                "parameters": trial.parameters,
                "in_sample_score": trial.in_sample_score,
                "out_of_sample_score": trial.out_of_sample_score,
                "robust_score": trial.robust_score,
            }
            for trial in result.trials
        ],
    }


@router.post("/tasks", status_code=201)
async def create_optimization_task(
    request: BacktestOptimizationTaskRequest, session: SessionDep
) -> dict[str, object]:
    task, created = await optimization_service.create_task(
        session, request.config, idempotency_key=request.idempotency_key
    )
    if request.run and task.status not in {"succeeded", "cancelled"}:
        try:
            task = await optimization_service.run_task(session, task.id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"task": optimization_task_to_dict(task), "created": created}


@router.get("/tasks")
async def list_optimization_tasks(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    rows = (
        await session.scalars(
            select(OptimizationTaskRecord)
            .order_by(OptimizationTaskRecord.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {"records": [optimization_task_to_dict(row) for row in rows], "count": len(rows)}


@router.post("/tasks/recover")
async def recover_optimization_tasks(session: SessionDep) -> dict[str, int]:
    return {"recovered": await optimization_service.recover_interrupted(session)}


@router.get("/tasks/{task_id}")
async def get_optimization_task(task_id: int, session: SessionDep) -> dict[str, object]:
    task = await session.get(OptimizationTaskRecord, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="optimization task not found")
    return {
        "task": optimization_task_to_dict(task),
        "result": json.loads(task.result_json) if task.result_json else None,
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_optimization_task(task_id: int, session: SessionDep) -> dict[str, object]:
    try:
        task = await optimization_service.cancel_task(session, task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="optimization task not found") from exc
    return optimization_task_to_dict(task)
