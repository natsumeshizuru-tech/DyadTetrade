from __future__ import annotations

import importlib
import itertools
import random
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

Objective = Callable[[dict[str, float | int], str], float]
MultiObjective = Callable[[dict[str, float | int], str], dict[str, float]]


@dataclass(frozen=True, slots=True)
class OptimizationTrial:
    number: int
    parameters: dict[str, float | int]
    in_sample_score: float
    out_of_sample_score: float
    complexity_penalty: float
    robust_score: float


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    method: str
    trials: tuple[OptimizationTrial, ...]
    best: OptimizationTrial
    neighborhood_stability: float
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MultiObjectiveTrial:
    number: int
    parameters: dict[str, float | int]
    in_sample_objectives: dict[str, float]
    out_of_sample_objectives: dict[str, float]
    robust_score: float


@dataclass(frozen=True, slots=True)
class MultiObjectiveResult:
    method: str
    trials: tuple[MultiObjectiveTrial, ...]
    pareto_front: tuple[MultiObjectiveTrial, ...]
    best: MultiObjectiveTrial
    warnings: tuple[str, ...]


class OptimizationEngine:
    def run(
        self,
        parameter_space: dict[str, list[float | int]],
        objective: Objective,
        *,
        method: Literal["grid", "random", "optuna"] = "grid",
        max_trials: int = 20,
        random_seed: int = 0,
        complexity_weight: float = 0.01,
        parallel_workers: int = 1,
        cached_trials: dict[str, OptimizationTrial] | None = None,
        trial_callback: Callable[[OptimizationTrial], None] | None = None,
    ) -> OptimizationResult:
        if not parameter_space or any(not values for values in parameter_space.values()):
            raise ValueError("parameter space must contain non-empty value lists")
        optuna_study: Any = None
        optuna_trials: list[Any] = []
        if method == "optuna":
            optuna: Any = importlib.import_module("optuna")
            sampler = optuna.samplers.TPESampler(seed=random_seed)
            optuna_study = optuna.create_study(direction="maximize", sampler=sampler)
            candidates = []
            for _ in range(max_trials):
                trial = optuna_study.ask()
                candidates.append(
                    {
                        name: trial.suggest_categorical(name, parameter_space[name])
                        for name in sorted(parameter_space)
                    }
                )
                optuna_trials.append(trial)
        else:
            candidates = self._candidates(
                parameter_space,
                method=method,
                max_trials=max_trials,
                random_seed=random_seed,
            )
        if parallel_workers < 1:
            raise ValueError("parallel_workers must be at least one")

        def evaluate(item: tuple[int, dict[str, float | int]]) -> OptimizationTrial:
            number, parameters = item
            cache_key = self.parameter_key(parameters)
            if cached_trials is not None and cache_key in cached_trials:
                cached = cached_trials[cache_key]
                return OptimizationTrial(
                    number,
                    dict(parameters),
                    cached.in_sample_score,
                    cached.out_of_sample_score,
                    cached.complexity_penalty,
                    cached.robust_score,
                )
            in_sample = float(objective(parameters, "in_sample"))
            out_of_sample = float(objective(parameters, "out_of_sample"))
            complexity = complexity_weight * len(parameters)
            robust = out_of_sample - complexity - abs(in_sample - out_of_sample) * 0.25
            return OptimizationTrial(
                number, dict(parameters), in_sample, out_of_sample, complexity, robust
            )

        numbered = list(enumerate(candidates))
        if parallel_workers == 1:
            trials = []
            for item in numbered:
                trial_result = evaluate(item)
                trials.append(trial_result)
                if trial_callback is not None:
                    trial_callback(trial_result)
        else:
            with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                trials = []
                for trial_result in executor.map(evaluate, numbered):
                    trials.append(trial_result)
                    if trial_callback is not None:
                        trial_callback(trial_result)
        for number, trial_result in enumerate(trials):
            if optuna_study is not None:
                optuna_study.tell(optuna_trials[number], trial_result.robust_score)
        if not trials:
            raise ValueError("optimization generated no trials")
        best = max(trials, key=lambda trial: trial.robust_score)
        distances = []
        neighbor_scores = []
        best_vector = np.array([float(best.parameters[key]) for key in sorted(best.parameters)])
        for trial in trials:
            vector = np.array([float(trial.parameters[key]) for key in sorted(trial.parameters)])
            distances.append(float(np.linalg.norm(vector - best_vector)))
        positive = sorted(value for value in distances if value > 0)
        threshold = positive[min(2, len(positive) - 1)] if positive else 0.0
        for distance, trial in zip(distances, trials, strict=True):
            if distance <= threshold:
                neighbor_scores.append(trial.out_of_sample_score)
        neighborhood_stability = (
            float(np.mean(neighbor_scores)) if neighbor_scores else best.out_of_sample_score
        )
        warnings = (
            "Multiple-testing risk: validate the selected neighborhood on untouched data.",
            "The selected trial requires out-of-sample and cost sensitivity evidence.",
        )
        return OptimizationResult(method, tuple(trials), best, neighborhood_stability, warnings)

    def run_multiobjective(
        self,
        parameter_space: dict[str, list[float | int]],
        objective: MultiObjective,
        *,
        weights: dict[str, float],
        method: Literal["grid", "random", "optuna"] = "grid",
        max_trials: int = 20,
        random_seed: int = 0,
        parallel_workers: int = 1,
    ) -> MultiObjectiveResult:
        if not weights or all(weight == 0 for weight in weights.values()):
            raise ValueError("multi-objective weights must contain a non-zero value")
        captured: dict[tuple[tuple[str, float | int], ...], dict[str, dict[str, float]]] = {}

        def scalar(parameters: dict[str, float | int], segment: str) -> float:
            values = objective(parameters, segment)
            missing = sorted(set(weights).difference(values))
            if missing:
                raise ValueError(f"multi-objective result missing metrics: {missing}")
            key = tuple(sorted(parameters.items()))
            captured.setdefault(key, {})[segment] = dict(values)
            return float(sum(values[name] * weight for name, weight in weights.items()))

        scalar_result = self.run(
            parameter_space,
            scalar,
            method=method,
            max_trials=max_trials,
            random_seed=random_seed,
            complexity_weight=0.0,
            parallel_workers=parallel_workers,
        )
        trials = tuple(
            MultiObjectiveTrial(
                trial.number,
                trial.parameters,
                captured[tuple(sorted(trial.parameters.items()))]["in_sample"],
                captured[tuple(sorted(trial.parameters.items()))]["out_of_sample"],
                trial.robust_score,
            )
            for trial in scalar_result.trials
        )
        pareto = tuple(
            trial
            for trial in trials
            if not any(
                self._dominates(other, trial, tuple(weights))
                for other in trials
                if other.number != trial.number
            )
        )
        best = max(trials, key=lambda item: item.robust_score)
        return MultiObjectiveResult(
            method,
            trials,
            pareto,
            best,
            (
                "Pareto membership does not replace untouched out-of-sample validation.",
                "Objective weights and nearby parameter performance must be reported together.",
            ),
        )

    @staticmethod
    def _dominates(
        candidate: MultiObjectiveTrial,
        reference: MultiObjectiveTrial,
        names: tuple[str, ...],
    ) -> bool:
        candidate_values = candidate.out_of_sample_objectives
        reference_values = reference.out_of_sample_objectives
        return all(candidate_values[name] >= reference_values[name] for name in names) and any(
            candidate_values[name] > reference_values[name] for name in names
        )

    @staticmethod
    def parameter_key(parameters: dict[str, float | int]) -> str:
        return repr(tuple(sorted(parameters.items())))

    @staticmethod
    def _candidates(
        parameter_space: dict[str, list[float | int]],
        *,
        method: str,
        max_trials: int,
        random_seed: int,
    ) -> list[dict[str, float | int]]:
        names = sorted(parameter_space)
        grid = [
            dict(zip(names, values, strict=True))
            for values in itertools.product(*(parameter_space[name] for name in names))
        ]
        if method == "grid":
            return grid[:max_trials]
        rng = random.Random(random_seed)
        rng.shuffle(grid)
        if method == "random":
            return grid[:max_trials]
        raise ValueError(f"unsupported optimization method: {method}")
