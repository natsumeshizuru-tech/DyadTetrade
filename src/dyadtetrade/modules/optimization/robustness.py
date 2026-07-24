from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class RobustnessScenario:
    name: str
    commission_multiplier: float = 1.0
    slippage_bps: float = 0.0
    execution_delay_bars: int = 1
    volume_participation: float = 1.0
    start_offset_bars: int = 0


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: RobustnessScenario
    score: float


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    fold: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    score: float


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    scenarios: tuple[ScenarioResult, ...]
    walk_forward: tuple[WalkForwardResult, ...]
    monte_carlo: dict[str, float]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "scenarios": [
                {"scenario": asdict(item.scenario), "score": item.score} for item in self.scenarios
            ],
            "walk_forward": [asdict(item) for item in self.walk_forward],
            "monte_carlo": self.monte_carlo,
            "warnings": list(self.warnings),
        }


ScenarioEvaluator = Callable[[pd.DataFrame, RobustnessScenario], float]


class RobustnessEngine:
    DEFAULT_SCENARIOS = (
        RobustnessScenario("base"),
        RobustnessScenario("double_commission", commission_multiplier=2.0),
        RobustnessScenario("slippage_5bps", slippage_bps=5.0),
        RobustnessScenario("delay_two_bars", execution_delay_bars=2),
        RobustnessScenario("capacity_5pct", volume_participation=0.05),
        RobustnessScenario("start_one_bar_later", start_offset_bars=1),
    )

    def run(
        self,
        frame: pd.DataFrame,
        evaluator: ScenarioEvaluator,
        *,
        returns: pd.Series,
        walk_forward_folds: int = 3,
        monte_carlo_simulations: int = 200,
        random_seed: int = 0,
    ) -> RobustnessReport:
        if frame.empty:
            raise ValueError("robustness analysis requires market data")
        scenarios = tuple(
            ScenarioResult(
                scenario,
                float(evaluator(frame.iloc[scenario.start_offset_bars :], scenario)),
            )
            for scenario in self.DEFAULT_SCENARIOS
        )
        folds = self._walk_forward_splits(frame, walk_forward_folds)
        walk_forward = tuple(
            WalkForwardResult(
                fold,
                str(pd.to_datetime(train["datetime"]).min()),
                str(pd.to_datetime(train["datetime"]).max()),
                str(pd.to_datetime(test["datetime"]).min()),
                str(pd.to_datetime(test["datetime"]).max()),
                float(evaluator(test, RobustnessScenario(f"walk_forward_{fold}"))),
            )
            for fold, train, test in folds
        )
        monte_carlo = self.monte_carlo(
            returns,
            simulations=monte_carlo_simulations,
            random_seed=random_seed,
        )
        return RobustnessReport(
            scenarios,
            walk_forward,
            monte_carlo,
            (
                "Walk-forward folds preserve time order and never shuffle observations.",
                "Monte Carlo resampling measures path sensitivity, not future expected return.",
                "Multiple-testing and capacity assumptions remain explicit release gates.",
            ),
        )

    @staticmethod
    def _walk_forward_splits(
        frame: pd.DataFrame, folds: int
    ) -> list[tuple[int, pd.DataFrame, pd.DataFrame]]:
        if folds < 1:
            raise ValueError("walk_forward_folds must be at least one")
        dates = sorted(pd.to_datetime(frame["datetime"]).dt.normalize().unique())
        if len(dates) < folds + 3:
            return []
        initial_train = max(2, len(dates) // 2)
        remaining = len(dates) - initial_train
        test_size = max(1, remaining // folds)
        results: list[tuple[int, pd.DataFrame, pd.DataFrame]] = []
        normalized = pd.to_datetime(frame["datetime"]).dt.normalize()
        for fold in range(folds):
            test_start = initial_train + fold * test_size
            test_end = len(dates) if fold == folds - 1 else min(len(dates), test_start + test_size)
            if test_start >= len(dates) or test_end <= test_start:
                continue
            train_dates = set(dates[:test_start])
            test_dates = set(dates[test_start:test_end])
            train = frame.loc[normalized.isin(train_dates)].copy()
            test = frame.loc[normalized.isin(test_dates)].copy()
            if not train.empty and not test.empty:
                results.append((fold, train, test))
        return results

    @staticmethod
    def monte_carlo(
        returns: pd.Series,
        *,
        simulations: int = 200,
        random_seed: int = 0,
    ) -> dict[str, float]:
        clean = returns.astype(float).replace([np.inf, -np.inf], np.nan).dropna().to_numpy()
        if len(clean) == 0 or simulations < 1:
            return {
                "simulations": 0.0,
                "terminal_p05": 0.0,
                "terminal_median": 0.0,
                "terminal_p95": 0.0,
                "probability_of_loss": 0.0,
                "max_drawdown_p95": 0.0,
            }
        rng = np.random.default_rng(random_seed)
        terminals: list[float] = []
        drawdowns: list[float] = []
        for _ in range(simulations):
            sample = rng.choice(clean, size=len(clean), replace=True)
            curve = np.cumprod(1 + sample)
            terminals.append(float(curve[-1] - 1))
            peaks = np.maximum.accumulate(curve)
            drawdowns.append(abs(float(np.min(curve / peaks - 1))))
        return {
            "simulations": float(simulations),
            "terminal_p05": float(np.quantile(terminals, 0.05)),
            "terminal_median": float(np.quantile(terminals, 0.5)),
            "terminal_p95": float(np.quantile(terminals, 0.95)),
            "probability_of_loss": float(np.mean(np.asarray(terminals) < 0)),
            "max_drawdown_p95": float(np.quantile(drawdowns, 0.95)),
        }
