from __future__ import annotations

import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from dyadtetrade.modules.strategy.base import Strategy
from dyadtetrade.modules.strategy.examples import (
    BuyAndHoldStrategy,
    EqualWeightRotationStrategy,
    EtfMultiAssetAllocationStrategy,
    MovingAverageCrossStrategy,
    TickMomentumSmallCapStrategy,
)


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    name: str
    version: str
    description: str
    parameters_schema: dict[str, Any]
    enabled: bool


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, type[Strategy]] = {}
        self._enabled: dict[str, bool] = {}

    def register(self, strategy_class: type[Strategy], *, enabled: bool = True) -> None:
        if not strategy_class.name or strategy_class.name == "base":
            raise ValueError("strategy must declare a unique name")
        existing = self._strategies.get(strategy_class.name)
        if existing is not None and existing is not strategy_class:
            raise ValueError(f"strategy already registered: {strategy_class.name}")
        self._strategies[strategy_class.name] = strategy_class
        self._enabled[strategy_class.name] = enabled

    def create(
        self, name: str, strategy_id: str, parameters: dict[str, Any] | None = None
    ) -> Strategy:
        if name not in self._strategies:
            raise KeyError(f"unknown strategy: {name}")
        if not self._enabled[name]:
            raise PermissionError(f"strategy is disabled: {name}")
        return self._strategies[name](strategy_id, parameters)

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._strategies:
            raise KeyError(name)
        self._enabled[name] = enabled

    def list_strategies(self) -> list[StrategyMetadata]:
        return [
            StrategyMetadata(
                name=name,
                version=strategy.version,
                description=strategy.description,
                parameters_schema=strategy.parameters_model.model_json_schema(),
                enabled=self._enabled[name],
            )
            for name, strategy in sorted(self._strategies.items())
        ]

    def discover(self, directory: Path) -> list[str]:
        loaded: list[str] = []
        if not directory.exists():
            return loaded
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module = self._load_module(path)
            for _, value in inspect.getmembers(module, inspect.isclass):
                is_local_strategy = (
                    value is not Strategy
                    and issubclass(value, Strategy)
                    and value.__module__ == module.__name__
                )
                if is_local_strategy:
                    self.register(value)
                    loaded.append(value.name)
        return loaded

    @staticmethod
    def _load_module(path: Path) -> ModuleType:
        module_name = f"dyadtetrade_user_strategy_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load strategy module: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


strategy_registry = StrategyRegistry()
for builtin in (
    BuyAndHoldStrategy,
    MovingAverageCrossStrategy,
    EqualWeightRotationStrategy,
    EtfMultiAssetAllocationStrategy,
    TickMomentumSmallCapStrategy,
):
    strategy_registry.register(builtin)
