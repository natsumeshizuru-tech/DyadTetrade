from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from dyadtetrade.api.legacy_app import app
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.modules.backtest import BacktestConfig
from dyadtetrade.modules.optimization import (
    BacktestOptimizationConfig,
    BacktestOptimizationService,
    OptimizationEngine,
    OptimizationTrial,
    RobustnessEngine,
)


def optimization_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=24)
    return pd.DataFrame(
        {
            "datetime": dates,
            "symbol": "510300.SH",
            "open": [4 + index * 0.01 for index in range(len(dates))],
            "high": [4.03 + index * 0.01 for index in range(len(dates))],
            "low": [3.98 + index * 0.01 for index in range(len(dates))],
            "close": [
                4.01 + index * 0.01 + (0.02 if index % 4 == 0 else 0) for index in range(len(dates))
            ],
            "volume": 1_000_000,
            "amount": 4_000_000,
            "suspended": False,
        }
    )


def optimization_config(*, multi: bool = True) -> BacktestOptimizationConfig:
    return BacktestOptimizationConfig(
        base_backtest=BacktestConfig(
            strategy_name="moving_average_cross",
            strategy_parameters={"symbol": "510300.SH", "quantity": 1000},
            symbols=["510300.SH"],
            start_date=date(2026, 1, 5),
            end_date=date(2026, 2, 10),
            initial_cash=Decimal("100000"),
        ),
        parameter_space={"fast": [2, 3], "slow": [4, 5]},
        method="grid",
        max_trials=4,
        parallel_workers=2,
        random_seed=11,
        objective_weights=(
            {"total_return": 1.0, "max_drawdown": 0.2} if multi else {"total_return": 1.0}
        ),
        walk_forward_folds=2,
        monte_carlo_simulations=20,
    )


def test_parallel_and_multiobjective_search_is_deterministic() -> None:
    engine = OptimizationEngine()

    def objective(parameters: dict[str, float | int], segment: str) -> dict[str, float]:
        x = float(parameters["x"])
        adjustment = 0.0 if segment == "in_sample" else 0.1
        return {"return": -((x - 2) ** 2) - adjustment, "risk": -abs(x - 1)}

    first = engine.run_multiobjective(
        {"x": [1, 2, 3]},
        objective,
        weights={"return": 1.0, "risk": 0.1},
        parallel_workers=2,
    )
    second = engine.run_multiobjective(
        {"x": [1, 2, 3]},
        objective,
        weights={"return": 1.0, "risk": 0.1},
        parallel_workers=2,
    )
    assert first.best.parameters == second.best.parameters == {"x": 2}
    assert first.pareto_front


def test_scalar_search_resumes_completed_parameter_trials() -> None:
    engine = OptimizationEngine()
    checkpoint: dict[str, OptimizationTrial] = {}

    def objective(parameters: dict[str, float | int], segment: str) -> float:
        return float(parameters["x"]) + (0.0 if segment == "in_sample" else 0.5)

    engine.run(
        {"x": [1, 2]},
        objective,
        max_trials=2,
        trial_callback=lambda trial: checkpoint.__setitem__(
            engine.parameter_key(trial.parameters), trial
        ),
    )
    calls = 0

    def resumed_objective(parameters: dict[str, float | int], segment: str) -> float:
        nonlocal calls
        calls += 1
        return objective(parameters, segment)

    resumed = engine.run(
        {"x": [1, 2, 3]},
        resumed_objective,
        max_trials=3,
        cached_trials=checkpoint,
    )
    assert len(resumed.trials) == 3
    assert calls == 2


def test_robustness_monte_carlo_is_reproducible_and_time_ordered() -> None:
    frame = optimization_frame()
    engine = RobustnessEngine()
    report = engine.run(
        frame,
        lambda selected, scenario: float(len(selected)) - scenario.slippage_bps,
        returns=pd.Series([0.01, -0.02, 0.005, 0.01]),
        walk_forward_folds=2,
        monte_carlo_simulations=50,
        random_seed=7,
    )
    repeated = engine.monte_carlo(
        pd.Series([0.01, -0.02, 0.005, 0.01]), simulations=50, random_seed=7
    )
    assert report.monte_carlo == repeated
    assert len(report.scenarios) == 6
    assert len(report.walk_forward) == 2
    assert all(item.train_end < item.test_start for item in report.walk_forward)


@pytest.mark.asyncio
async def test_backtest_optimization_task_persists_multiobjective_and_robustness(
    tmp_path: Path,
) -> None:
    await initialize_database()
    service = BacktestOptimizationService(output_root=tmp_path)
    key = f"optimization-test-{uuid4().hex}"
    async with SessionFactory() as session:
        task, created = await service.create_task(
            session, optimization_config(), idempotency_key=key
        )
        completed = await service.run_task(session, task.id, frame=optimization_frame())
        duplicate, duplicate_created = await service.create_task(
            session, optimization_config(), idempotency_key=key
        )
    assert created is True
    assert duplicate_created is False
    assert duplicate.id == completed.id
    assert completed.status == "succeeded"
    assert completed.completed_trials == 4
    assert completed.progress == 100
    assert Path(completed.result_path or "").exists()
    payload = json.loads(completed.result_json or "{}")
    assert payload["optimization"]["pareto_front"]
    assert len(payload["robustness"]["scenarios"]) == 6
    assert payload["robustness"]["monte_carlo"]["simulations"] == 20


def test_optimization_task_api_can_create_and_cancel_without_running() -> None:
    key = uuid4().hex
    payload = {
        "config": json.loads(optimization_config(multi=False).model_dump_json()),
        "idempotency_key": key,
        "run": False,
    }
    with TestClient(app) as client:
        created = client.post("/api/v1/optimizations/tasks", json=payload)
        assert created.status_code == 201, created.text
        task = created.json()["task"]
        assert task["status"] == "pending"
        cancelled = client.post(f"/api/v1/optimizations/tasks/{task['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
