from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from dyadtetrade.data.backtest.broker import HistoricalBroker
from dyadtetrade.data.backtest.context_info import ContextInfo
from dyadtetrade.data.backtest.engine import QmtBacktestEngine
from dyadtetrade.data.backtest.models import HistoricalBacktestSettings
from dyadtetrade.data.backtest.repository import BacktestResultRepository
from dyadtetrade.data.compat.xtdata_compat import LocalXtData
from dyadtetrade.data.config import DataPlatformSettings, StorageConfig
from dyadtetrade.data.models import BarRecord
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def epoch(value: str) -> int:
    parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ)
    return int(parsed.timestamp() * 1000)


def minute(value: str, open_price: float, close: float) -> BarRecord:
    return BarRecord(
        symbol="600000.SH",
        ts=epoch(value),
        trade_date=int(value[:8]),
        open=open_price,
        high=max(open_price, close) + 0.1,
        low=min(open_price, close) - 0.1,
        close=close,
        volume=10000,
        amount=close * 10000,
        pre_close=9.8,
        suspend_flag=0,
        ingested_at=epoch("20260718010000"),
        source_hash=value,
    )


def local_data(tmp_path: Path) -> LocalXtData:
    settings = DataPlatformSettings(storage=StorageConfig(root=tmp_path / "sqlite"))
    catalog = CatalogRepository(settings.storage)
    catalog.initialize()
    catalog.upsert_calendar("SH", [20260715, 20260716], source="test")
    BarRepository(settings.storage).upsert(
        "1m",
        [
            minute("20260715093100", 10.0, 10.0),
            minute("20260715093200", 10.1, 10.2),
            minute("20260715093300", 10.2, 10.3),
            minute("20260716093100", 10.4, 10.5),
            minute("20260716093200", 10.6, 10.7),
        ],
    )
    return LocalXtData(settings)


class LeakCheckingStrategy:
    def __init__(self) -> None:
        self.observed_last: list[pd.Timestamp] = []

    def init(self, context: ContextInfo) -> None:
        context.set_universe(["600000.SH"])

    def handlebar(self, context: ContextInfo) -> None:
        frame = context.get_market_data_ex(
            ["close"],
            context.get_universe(),
            period="1m",
            count=-1,
            dividend_type="front_ratio",
            fill_data=False,
        )["600000.SH"]
        self.observed_last.append(frame.index[-1])
        if context.barpos == 0:
            context.order("600000.SH", 100, "buy")
        elif context.barpos == 1:
            context.order("600000.SH", 100, "sell")
        elif context.barpos == 3:
            context.order("600000.SH", 100, "sell")


def test_qmt_style_backtest_has_no_future_data_uses_next_bar_and_persists(
    tmp_path: Path,
) -> None:
    data = local_data(tmp_path)
    strategy = LeakCheckingStrategy()
    engine = QmtBacktestEngine(data, result_root=tmp_path / "results")

    result = engine.run(
        strategy,
        symbols=["600000.SH"],
        period="1m",
        start="20260715093100",
        end="20260716093200",
        strategy_name="leak-check",
    )

    expected = [
        pd.Timestamp("2026-07-15 09:31:00"),
        pd.Timestamp("2026-07-15 09:32:00"),
        pd.Timestamp("2026-07-15 09:33:00"),
        pd.Timestamp("2026-07-16 09:31:00"),
        pd.Timestamp("2026-07-16 09:32:00"),
    ]
    assert strategy.observed_last == expected
    assert result.bars_processed == 5
    assert [order.status for order in result.orders] == ["filled", "rejected", "filled"]
    assert result.orders[0].filled_ts == epoch("20260715093200")
    assert result.orders[1].reason == "insufficient_available_position"
    assert result.orders[2].filled_ts == epoch("20260716093200")
    assert len(result.trades) == 2
    repository = BacktestResultRepository(Path(result.result_database))
    assert repository.integrity() == "ok"
    with sqlite3.connect(result.result_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_equity").fetchone() == (5,)
        assert connection.execute("SELECT COUNT(*) FROM backtest_trades").fetchone() == (2,)


def test_research_and_backtest_use_the_same_local_query(tmp_path: Path) -> None:
    data = local_data(tmp_path)
    research = data.get_market_data_ex(
        ["close"],
        ["600000.SH"],
        "1m",
        "20260715093100",
        "20260716093200",
        -1,
        "none",
        False,
    )["600000.SH"]
    strategy = LeakCheckingStrategy()
    QmtBacktestEngine(data, result_root=tmp_path / "results").run(
        strategy,
        symbols=["600000.SH"],
        period="1m",
        start="20260715093100",
        end="20260716093200",
    )
    assert len(research) == len(strategy.observed_last) == 5
    assert research.index.tolist() == strategy.observed_last


def test_broker_enforces_lot_suspend_limit_fees_and_t1() -> None:
    broker = HistoricalBroker(
        HistoricalBacktestSettings(
            initial_cash=Decimal("100000"),
            commission_rate=Decimal("0.001"),
            minimum_commission=Decimal("5"),
            stamp_duty_rate=Decimal("0.0005"),
            slippage_bps=Decimal("10"),
        )
    )
    broker.begin_trade_date(20260715)
    broker.submit("600000.SH", "buy", 50, epoch("20260715093100"))
    broker.submit("600000.SH", "buy", 100, epoch("20260715093100"))
    broker.execute_pending(
        {
            "600000.SH": {
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "pre_close": 9.8,
                "suspend_flag": 1,
            }
        },
        epoch("20260715093200"),
    )
    broker.submit("600000.SH", "buy", 100, epoch("20260715093300"))
    broker.execute_pending(
        {
            "600000.SH": {
                "open": 10.78,
                "high": 10.78,
                "low": 10.78,
                "pre_close": 9.8,
                "suspend_flag": 0,
            }
        },
        epoch("20260715093400"),
    )
    assert [order.reason for order in broker.orders] == [
        "buy_quantity_must_match_lot_size",
        "suspended",
        "locked_at_price_limit",
    ]
