from __future__ import annotations

from dyadtetrade.data.backtest.context_info import ContextInfo


class DoubleMovingAverageStrategy:
    def __init__(self, symbol: str, *, fast: int = 5, slow: int = 20, quantity: int = 100) -> None:
        if fast <= 0 or slow <= fast:
            raise ValueError("moving-average windows require 0 < fast < slow")
        self.symbol = symbol.upper()
        self.fast = fast
        self.slow = slow
        self.quantity = quantity

    def init(self, context: ContextInfo) -> None:
        context.set_universe([self.symbol])

    def handlebar(self, context: ContextInfo) -> None:
        frames = context.get_market_data_ex(
            ["close"],
            [self.symbol],
            period=context.period,
            dividend_type="front_ratio",
            count=self.slow,
            fill_data=False,
        )
        closes = frames[self.symbol]["close"]
        if len(closes) < self.slow:
            return
        fast_value = float(closes.tail(self.fast).mean())
        slow_value = float(closes.mean())
        account = context.account()
        position = int(account["positions"].get(self.symbol, {}).get("quantity", 0))
        if fast_value > slow_value and position == 0:
            context.order(self.symbol, self.quantity, "buy")
        elif fast_value < slow_value and position > 0:
            context.order(self.symbol, position, "sell")
