from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.db.duckdb_store import query_bars
from dyadtetrade.db.models import OptimizationTaskRecord
from dyadtetrade.modules.backtest import BacktestConfig, BacktestEngine
from dyadtetrade.modules.optimization.engine import OptimizationEngine, OptimizationTrial
from dyadtetrade.modules.optimization.robustness import RobustnessEngine, RobustnessScenario


class BacktestOptimizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_backtest: BacktestConfig
    parameter_space: dict[str, list[float | int]]
    method: Literal["grid", "random", "optuna"] = "grid"
    max_trials: int = Field(default=20, ge=1, le=500)
    parallel_workers: int = Field(default=1, ge=1, le=8)
    random_seed: int = 0
    split_ratio: float = Field(default=0.7, gt=0.2, lt=0.9)
    objective_weights: dict[str, float] = Field(default_factory=lambda: {"total_return": 1.0})
    walk_forward_folds: int = Field(default=3, ge=1, le=10)
    monte_carlo_simulations: int = Field(default=200, ge=20, le=5000)

    @model_validator(mode="after")
    def validate_search(self) -> BacktestOptimizationConfig:
        if not self.parameter_space or any(not values for values in self.parameter_space.values()):
            raise ValueError("parameter_space must contain non-empty values")
        if not self.objective_weights or all(
            value == 0 for value in self.objective_weights.values()
        ):
            raise ValueError("objective_weights require at least one non-zero value")
        return self


class BacktestOptimizationService:
    def __init__(self, *, output_root: Path = Path("reports/optimizations")) -> None:
        self.output_root = output_root

    async def create_task(
        self,
        session: AsyncSession,
        config: BacktestOptimizationConfig,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[OptimizationTaskRecord, bool]:
        key = idempotency_key or hashlib.sha256(config.model_dump_json().encode()).hexdigest()
        existing = await session.scalar(
            select(OptimizationTaskRecord).where(OptimizationTaskRecord.idempotency_key == key)
        )
        if existing is not None:
            return existing, False
        candidate_count = math.prod(len(values) for values in config.parameter_space.values())
        total_trials = min(candidate_count, config.max_trials)
        task = OptimizationTaskRecord(
            idempotency_key=key,
            status="pending",
            method=config.method,
            config_json=config.model_dump_json(),
            objective_names_json=json.dumps(sorted(config.objective_weights)),
            progress=0.0,
            total_trials=total_trials,
            completed_trials=0,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task, True

    async def run_task(
        self,
        session: AsyncSession,
        task_id: int,
        *,
        frame: pd.DataFrame | None = None,
    ) -> OptimizationTaskRecord:
        task = await session.get(OptimizationTaskRecord, task_id)
        if task is None:
            raise LookupError(f"optimization task not found: {task_id}")
        if task.status == "succeeded" and task.result_json:
            return task
        if task.cancel_requested:
            task.status = "cancelled"
            task.finished_at = datetime.now()
            await session.commit()
            return task
        config = BacktestOptimizationConfig.model_validate_json(task.config_json)
        market_frame = frame if frame is not None else self._load_frame(config.base_backtest)
        in_sample, out_of_sample = self._split_frame(market_frame, config.split_ratio)
        task.status = "running"
        task.started_at = datetime.now()
        task.progress = 5.0
        task.error_message = None
        output_directory = self.output_root / f"task_{task.id}"
        output_directory.mkdir(parents=True, exist_ok=True)
        checkpoint_path = output_directory / "checkpoint.json"
        cached_trials = self._load_checkpoint(checkpoint_path)
        resumed_trial_count = len(cached_trials)
        if len(config.objective_weights) == 1:
            task.completed_trials = len(cached_trials)
            if task.total_trials:
                task.progress = max(task.progress, len(cached_trials) / task.total_trials * 60.0)
        await session.commit()
        try:

            def metrics_objective(
                parameters: dict[str, float | int], segment: str
            ) -> dict[str, float]:
                selected = in_sample if segment == "in_sample" else out_of_sample
                result = BacktestEngine().run(
                    self._backtest_config(config.base_backtest, parameters), selected
                )
                return {
                    name: float(result.metrics.get(name, 0.0)) for name in config.objective_weights
                }

            optimizer = OptimizationEngine()
            optimization_payload: dict[str, Any]
            if len(config.objective_weights) > 1:
                optimized = optimizer.run_multiobjective(
                    config.parameter_space,
                    metrics_objective,
                    weights=config.objective_weights,
                    method=config.method,
                    max_trials=config.max_trials,
                    random_seed=config.random_seed,
                    parallel_workers=config.parallel_workers,
                )
                best_parameters = optimized.best.parameters
                optimization_payload = {
                    "method": optimized.method,
                    "best": self._multi_trial(optimized.best),
                    "trials": [self._multi_trial(item) for item in optimized.trials],
                    "pareto_front": [self._multi_trial(item) for item in optimized.pareto_front],
                    "warnings": list(optimized.warnings),
                }
                task.completed_trials = len(optimized.trials)
            else:
                metric_name, weight = next(iter(config.objective_weights.items()))

                def scalar_objective(parameters: dict[str, float | int], segment: str) -> float:
                    return metrics_objective(parameters, segment)[metric_name] * weight

                optimized_scalar = optimizer.run(
                    config.parameter_space,
                    scalar_objective,
                    method=config.method,
                    max_trials=config.max_trials,
                    random_seed=config.random_seed,
                    parallel_workers=config.parallel_workers,
                    cached_trials=cached_trials,
                    trial_callback=lambda trial: self._write_checkpoint(
                        checkpoint_path, cached_trials, trial
                    ),
                )
                best_parameters = optimized_scalar.best.parameters
                optimization_payload = {
                    "method": optimized_scalar.method,
                    "best": self._scalar_trial(optimized_scalar.best),
                    "trials": [self._scalar_trial(item) for item in optimized_scalar.trials],
                    "pareto_front": [],
                    "neighborhood_stability": optimized_scalar.neighborhood_stability,
                    "warnings": list(optimized_scalar.warnings),
                }
                task.completed_trials = len(optimized_scalar.trials)
            task.progress = 70.0
            await session.commit()
            await session.refresh(task)
            if task.cancel_requested:
                task.status = "cancelled"
                task.finished_at = datetime.now()
                await session.commit()
                return task

            best_config = self._backtest_config(config.base_backtest, best_parameters)
            best_result = BacktestEngine().run(best_config, market_frame)
            equity = best_result.equity_frame()
            returns = equity["total_assets"].astype(float).pct_change().fillna(0.0)
            primary_metric = next(iter(config.objective_weights))

            def scenario_evaluator(
                selected_frame: pd.DataFrame, scenario: RobustnessScenario
            ) -> float:
                scenario_config = best_config.model_copy(
                    update={
                        "commission_rate": best_config.commission_rate
                        * Decimal(str(scenario.commission_multiplier)),
                        "slippage_bps": best_config.slippage_bps
                        + Decimal(str(scenario.slippage_bps)),
                        "execution_delay_bars": scenario.execution_delay_bars,
                        "max_volume_participation": min(
                            best_config.max_volume_participation,
                            Decimal(str(scenario.volume_participation)),
                        ),
                    }
                )
                scenario_result = BacktestEngine().run(scenario_config, selected_frame)
                return float(scenario_result.metrics.get(primary_metric, 0.0))

            robustness = RobustnessEngine().run(
                market_frame,
                scenario_evaluator,
                returns=returns,
                walk_forward_folds=config.walk_forward_folds,
                monte_carlo_simulations=config.monte_carlo_simulations,
                random_seed=config.random_seed,
            )
            payload: dict[str, Any] = {
                "config": json.loads(config.model_dump_json()),
                "optimization": optimization_payload,
                "heatmap": optimization_payload["trials"],
                "robustness": robustness.to_dict(),
                "best_backtest": {
                    "parameters": best_parameters,
                    "metrics": best_result.metrics,
                    "data_fingerprint": best_result.data_fingerprint,
                },
                "resumed_trials": (
                    resumed_trial_count if len(config.objective_weights) == 1 else 0
                ),
            }
            result_path = output_directory / "result.json"
            serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            result_path.write_text(serialized, encoding="utf-8")
            task.result_path = str(result_path)
            task.result_json = serialized
            task.status = "succeeded"
            task.progress = 100.0
            task.finished_at = datetime.now()
            await session.commit()
            return task
        except Exception as exc:
            await session.rollback()
            failed = await session.get(OptimizationTaskRecord, task_id)
            assert failed is not None
            failed.status = "failed"
            failed.error_message = f"{type(exc).__name__}: {exc}"
            failed.finished_at = datetime.now()
            await session.commit()
            raise

    async def cancel_task(self, session: AsyncSession, task_id: int) -> OptimizationTaskRecord:
        task = await session.get(OptimizationTaskRecord, task_id)
        if task is None:
            raise LookupError(task_id)
        task.cancel_requested = True
        if task.status == "pending":
            task.status = "cancelled"
            task.finished_at = datetime.now()
        await session.commit()
        return task

    @staticmethod
    async def recover_interrupted(session: AsyncSession) -> int:
        task_ids = list(
            await session.scalars(
                select(OptimizationTaskRecord.id).where(OptimizationTaskRecord.status == "running")
            )
        )
        if task_ids:
            await session.execute(
                update(OptimizationTaskRecord)
                .where(OptimizationTaskRecord.id.in_(task_ids))
                .values(status="pending", error_message="recovered after interrupted process")
            )
        await session.commit()
        return len(task_ids)

    @staticmethod
    def _split_frame(frame: pd.DataFrame, ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
        dates = sorted(pd.to_datetime(frame["datetime"]).dt.normalize().unique())
        if len(dates) < 4:
            raise ValueError("optimization requires at least four distinct bars")
        split_index = min(max(int(len(dates) * ratio), 2), len(dates) - 2)
        normalized = pd.to_datetime(frame["datetime"]).dt.normalize()
        return (
            frame.loc[normalized.isin(set(dates[:split_index]))].copy(),
            frame.loc[normalized.isin(set(dates[split_index:]))].copy(),
        )

    @staticmethod
    def _backtest_config(
        base: BacktestConfig, parameters: dict[str, float | int]
    ) -> BacktestConfig:
        return base.model_copy(
            update={"strategy_parameters": {**base.strategy_parameters, **parameters}}
        )

    @staticmethod
    def _load_frame(config: BacktestConfig) -> pd.DataFrame:
        records = query_bars(
            symbols=config.symbols,
            start_date=config.start_date.strftime("%Y%m%d") if config.start_date else "19000101",
            end_date=config.end_date.strftime("%Y%m%d") if config.end_date else "29991231",
            layer="bt_ready",
            limit=2_000_000,
        )
        if not records:
            raise ValueError("no bt_ready data found for optimization")
        return pd.DataFrame(records)

    @staticmethod
    def _scalar_trial(trial: Any) -> dict[str, Any]:
        return {
            "number": trial.number,
            "parameters": trial.parameters,
            "in_sample_score": trial.in_sample_score,
            "out_of_sample_score": trial.out_of_sample_score,
            "complexity_penalty": trial.complexity_penalty,
            "robust_score": trial.robust_score,
        }

    @staticmethod
    def _multi_trial(trial: Any) -> dict[str, Any]:
        return {
            "number": trial.number,
            "parameters": trial.parameters,
            "in_sample_objectives": trial.in_sample_objectives,
            "out_of_sample_objectives": trial.out_of_sample_objectives,
            "robust_score": trial.robust_score,
        }

    @classmethod
    def _load_checkpoint(cls, path: Path) -> dict[str, OptimizationTrial]:
        if not path.is_file():
            return {}
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("optimization checkpoint must contain a trial list")
        trials: dict[str, OptimizationTrial] = {}
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("parameters"), dict):
                raise ValueError("optimization checkpoint contains an invalid trial")
            parameters = {
                str(key): value
                for key, value in item["parameters"].items()
                if isinstance(value, int | float)
            }
            trial = OptimizationTrial(
                int(item["number"]),
                parameters,
                float(item["in_sample_score"]),
                float(item["out_of_sample_score"]),
                float(item["complexity_penalty"]),
                float(item["robust_score"]),
            )
            trials[OptimizationEngine.parameter_key(parameters)] = trial
        return trials

    @classmethod
    def _write_checkpoint(
        cls,
        path: Path,
        trials: dict[str, OptimizationTrial],
        trial: OptimizationTrial,
    ) -> None:
        trials[OptimizationEngine.parameter_key(trial.parameters)] = trial
        payload = [
            cls._scalar_trial(item) for item in sorted(trials.values(), key=lambda x: x.number)
        ]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


def optimization_task_to_dict(task: OptimizationTaskRecord) -> dict[str, Any]:
    return {
        "id": task.id,
        "status": task.status,
        "method": task.method,
        "progress": task.progress,
        "total_trials": task.total_trials,
        "completed_trials": task.completed_trials,
        "cancel_requested": task.cancel_requested,
        "objective_names": json.loads(task.objective_names_json),
        "result_path": task.result_path,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }
