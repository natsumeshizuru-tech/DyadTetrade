from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dyadtetrade.domain import Bar, Tick
from dyadtetrade.modules.strategy.base import Strategy, StrategyContext


class BuyAndHoldParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = "600519.SH"
    quantity: int = Field(default=100, gt=0)


class BuyAndHoldStrategy(Strategy):
    name = "buy_and_hold"
    version = "1.0.0"
    description = "Buy once on the next eligible bar and hold."
    parameters_model = BuyAndHoldParameters

    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        parameters = BuyAndHoldParameters.model_validate(self.parameters)
        if bar.symbol == parameters.symbol and not self.state.get("submitted"):
            context.buy(bar.symbol, parameters.quantity)
            self.state["submitted"] = True


class MovingAverageParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = "510300.SH"
    fast: int = Field(default=5, ge=2)
    slow: int = Field(default=20, ge=3)
    quantity: int = Field(default=1000, gt=0)

    @model_validator(mode="after")
    def validate_windows(self) -> MovingAverageParameters:
        if self.fast >= self.slow:
            raise ValueError("fast window must be less than slow window")
        return self


class MovingAverageCrossStrategy(Strategy):
    name = "moving_average_cross"
    version = "1.0.0"
    description = "Long-only moving-average cross using information visible at bar close."
    parameters_model = MovingAverageParameters

    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        parameters = MovingAverageParameters.model_validate(self.parameters)
        if bar.symbol != parameters.symbol:
            return
        history = context.history([bar.symbol], parameters.slow, ["close"])
        if len(history) < parameters.slow:
            return
        fast = float(history["close"].tail(parameters.fast).mean())
        slow = float(history["close"].tail(parameters.slow).mean())
        position = context.get_position(bar.symbol)
        if fast > slow and (position is None or position.quantity == 0):
            context.buy(bar.symbol, parameters.quantity)
        elif fast < slow and position is not None and position.available_quantity > 0:
            context.sell(bar.symbol, position.available_quantity)


class EqualWeightRotationParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str]
    lookback: int = Field(default=20, ge=2)
    top_n: int = Field(default=10, ge=1)
    rebalance_every: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def validate_universe(self) -> EqualWeightRotationParameters:
        if len(set(self.symbols)) < self.top_n:
            raise ValueError("top_n exceeds unique universe size")
        return self


class EqualWeightRotationStrategy(Strategy):
    name = "equal_weight_rotation"
    version = "1.0.0"
    description = "Cross-sectional momentum rotation with equal target weights."
    parameters_model = EqualWeightRotationParameters
    _seen_dates: ClassVar[set[str]] = set()

    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        parameters = EqualWeightRotationParameters.model_validate(self.parameters)
        date_key = context.now.date().isoformat()
        dates_seen = int(self.state.get("dates_seen", 0))
        last_date = str(self.state.get("last_date", ""))
        if date_key == last_date:
            return
        self.state["last_date"] = date_key
        dates_seen += 1
        self.state["dates_seen"] = dates_seen
        if dates_seen % parameters.rebalance_every:
            return
        history = context.history(parameters.symbols, parameters.lookback, ["symbol", "close"])
        if history.empty or "symbol" not in history:
            return
        scores: dict[str, float] = {}
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_index()
            if len(ordered) >= parameters.lookback and float(ordered["close"].iloc[0]) > 0:
                scores[str(symbol)] = float(
                    ordered["close"].iloc[-1] / ordered["close"].iloc[0] - 1
                )
        selected = [
            item[0] for item in sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ]
        selected = selected[: parameters.top_n]
        if not selected:
            return
        target = Decimal("1") / len(selected)
        for symbol in parameters.symbols:
            context.order_target_percent(symbol, target if symbol in selected else Decimal("0"))


class EtfMultiAssetParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_weights: dict[str, Decimal] = Field(
        default_factory=lambda: {
            "510300.SH": Decimal("0.25"),
            "510500.SH": Decimal("0.20"),
            "512100.SH": Decimal("0.15"),
            "159915.SZ": Decimal("0.15"),
        }
    )
    rebalance_every: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def validate_weights(self) -> EtfMultiAssetParameters:
        if not self.target_weights:
            raise ValueError("ETF allocation requires target weights")
        if any(weight < 0 for weight in self.target_weights.values()):
            raise ValueError("ETF target weights cannot be negative")
        if sum(self.target_weights.values()) > Decimal("1"):
            raise ValueError("ETF target weights cannot exceed one")
        return self


class EtfMultiAssetAllocationStrategy(Strategy):
    name = "etf_multi_asset_allocation"
    version = "1.0.0"
    description = "Periodic ETF target-weight allocation with an explicit cash buffer."
    parameters_model = EtfMultiAssetParameters

    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        parameters = EtfMultiAssetParameters.model_validate(self.parameters)
        date_key = context.now.date().isoformat()
        if self.state.get("last_date") == date_key:
            return
        self.state["last_date"] = date_key
        dates_seen = int(self.state.get("dates_seen", 0)) + 1
        self.state["dates_seen"] = dates_seen
        if dates_seen != 1 and dates_seen % parameters.rebalance_every:
            return
        for symbol, weight in parameters.target_weights.items():
            context.order_target_percent(symbol, weight)


def _is_main_board_symbol(symbol: str) -> bool:
    code, _, exchange = symbol.upper().partition(".")
    return (exchange == "SH" and code.startswith(("600", "601", "603", "605"))) or (
        exchange == "SZ" and code.startswith(("000", "001", "002", "003"))
    )


class TickMomentumSmallCapParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str]
    float_shares: dict[str, float]
    momentum_lookback_ticks: int = Field(default=20, ge=3, le=1000)
    small_cap_n: int = Field(default=20, ge=2, le=200)
    top_n: int = Field(default=5, ge=1, le=50)
    rebalance_every_ticks: int = Field(default=50, ge=1, le=10000)
    max_total_weight: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    max_single_weight: Decimal = Field(default=Decimal("0.05"), gt=0, le=Decimal("0.20"))
    minimum_momentum: float = 0.0

    @model_validator(mode="after")
    def validate_mainboard_universe(self) -> TickMomentumSmallCapParameters:
        unique = set(self.symbols)
        if not unique or any(not _is_main_board_symbol(symbol) for symbol in unique):
            raise ValueError(
                "tick momentum small-cap universe only accepts SH/SZ main-board stocks"
            )
        if self.top_n > self.small_cap_n or self.small_cap_n > len(unique):
            raise ValueError("require top_n <= small_cap_n <= unique symbol count")
        missing = unique - self.float_shares.keys()
        if missing or any(self.float_shares[symbol] <= 0 for symbol in unique):
            raise ValueError("positive float_shares are required for every symbol")
        return self


class TickMomentumSmallCapStrategy(Strategy):
    name = "tick_momentum_small_cap"
    version = "1.2.0"
    description = (
        "Long-only SH/SZ main-board strategy: select the smallest float-cap names, "
        "then rank by visible tick momentum. Intended for bounded paper/dry-run validation."
    )
    parameters_model = TickMomentumSmallCapParameters

    def on_tick(self, context: StrategyContext, tick: Tick) -> None:
        parameters = TickMomentumSmallCapParameters.model_validate(self.parameters)
        if tick.symbol not in parameters.symbols or tick.last_price <= 0:
            return
        prices = self.state.setdefault("prices", {})
        if not isinstance(prices, dict):
            raise ValueError("strategy price state is corrupted")
        symbol_prices = list(prices.get(tick.symbol, []))
        symbol_prices.append(float(tick.last_price))
        prices[tick.symbol] = symbol_prices[-parameters.momentum_lookback_ticks :]
        self.state["latest_tick_at"] = tick.timestamp.isoformat()
        tick_count = int(self.state.get("tick_count", 0)) + 1
        self.state["tick_count"] = tick_count
        if tick_count % parameters.rebalance_every_ticks:
            return
        if any(
            len(list(prices.get(symbol, []))) < parameters.momentum_lookback_ticks
            for symbol in parameters.symbols
        ):
            return

        market_caps: dict[str, float] = {}
        momentum: dict[str, float] = {}
        for symbol in parameters.symbols:
            history = list(prices[symbol])
            latest = float(history[-1])
            market_caps[symbol] = parameters.float_shares[symbol] * latest
            momentum[symbol] = latest / float(history[0]) - 1
        small_cap = sorted(market_caps, key=market_caps.__getitem__)[: parameters.small_cap_n]
        selected = [
            symbol
            for symbol in sorted(small_cap, key=momentum.__getitem__, reverse=True)
            if momentum[symbol] >= parameters.minimum_momentum
        ][: parameters.top_n]
        self.state["selected"] = selected
        self.state["momentum"] = momentum
        self.state["market_caps"] = market_caps
        self.state["last_rebalance_at"] = context.now.isoformat()
        context.record(
            selected_count=float(len(selected)),
            best_momentum=max((momentum[symbol] for symbol in selected), default=0.0),
        )
        target = (
            min(parameters.max_total_weight / len(selected), parameters.max_single_weight)
            if selected
            else Decimal("0")
        )
        for symbol in parameters.symbols:
            context.order_target_percent(symbol, target if symbol in selected else Decimal("0"))
