from dyadtetrade.domain import Bar
from dyadtetrade.modules.strategy.base import Strategy, StrategyContext


class OpeningMvpStrategy(Strategy):
    name = "opening_mvp_strategy"
    version = "1.0.0"
    description = "Pre-open validated strategy"

    def on_bar(self, context: StrategyContext, bar: Bar) -> None:
        if context.get_position(bar.symbol) is None:
            context.buy(bar.symbol, 100)
