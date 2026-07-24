from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from dyadtetrade.data.adjustments import adjust_prices
from dyadtetrade.data.aggregation import aggregate_bars
from dyadtetrade.data.compat.xtdata_compat import LocalXtData
from dyadtetrade.data.config import DataPlatformSettings, StorageConfig
from dyadtetrade.data.models import BarRecord, CorporateActionRecord
from dyadtetrade.data.normalizer import normalize_xtdata_frame
from dyadtetrade.data.quality import hard_failures, validate_bars
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def ts(value: str) -> int:
    return int(datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ).timestamp() * 1000)


def stored_bar(symbol: str, value: str, close: float, *, volume: int = 100) -> BarRecord:
    return BarRecord(
        symbol=symbol,
        ts=ts(value),
        trade_date=int(value[:8]),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        amount=close * volume,
        pre_close=close,
        suspend_flag=0,
        ingested_at=ts("20260718010000"),
        source_hash=f"{symbol}-{value}-{close}",
    )


def test_normalizer_uses_named_fields_and_utc_epoch_without_mutating_input() -> None:
    source = pd.DataFrame(
        {
            "close": [10.2],
            "amount": [10200.0],
            "open": [10.0],
            "volume": [1000],
            "low": [9.9],
            "high": [10.3],
            "preClose": [9.8],
            "suspendFlag": [0],
        },
        index=["20260717093100"],
    )
    original = source.copy(deep=True)

    records = normalize_xtdata_frame(source, symbol="600000.SH", period="1m")

    assert source.equals(original)
    assert records[0].trade_date == 20260717
    assert records[0].ts == ts("20260717093100")
    assert records[0].close == 10.2
    assert records[0].pre_close == 9.8
    assert records[0].source_hash


def test_quality_reports_hard_ohlc_and_calendar_failures() -> None:
    invalid = BarRecord(
        symbol="BAD",
        ts=ts("20260718093100"),
        trade_date=20260718,
        open=10,
        high=9,
        low=11,
        close=10,
        volume=-1,
        amount=-2,
        suspend_flag=7,
    )
    issues = validate_bars([invalid], period="1m", requested_end=20260717, open_days={20260717})
    rules = {issue.rule for issue in hard_failures(issues)}
    assert {
        "invalid_symbol",
        "after_requested_end",
        "not_open_trading_day",
        "invalid_high",
        "invalid_low",
        "negative_volume",
        "negative_amount",
        "invalid_suspend_flag",
    } <= rules


def test_five_adjustment_modes_are_point_in_time_and_do_not_change_volume() -> None:
    bars = pd.DataFrame(
        {
            "trade_date": [20240102, 20240103, 20240104],
            "open": [10.0, 9.0, 9.5],
            "high": [10.0, 9.0, 9.5],
            "low": [10.0, 9.0, 9.5],
            "close": [10.0, 9.0, 9.5],
            "pre_close": [9.8, 10.0, 9.0],
            "volume": [100, 200, 300],
            "amount": [1000.0, 1800.0, 2850.0],
        }
    )
    actions = pd.DataFrame(
        {
            "action_date": [20240103],
            "interest": [1.0],
            "stock_bonus": [0.1],
            "stock_gift": [0.0],
            "allot_num": [0.0],
            "allot_price": [0.0],
            "dr": [2.0],
        }
    )
    original = bars.copy(deep=True)

    none = adjust_prices(bars, actions, mode="none")
    front_ratio = adjust_prices(bars, actions, mode="front_ratio")
    back_ratio = adjust_prices(bars, actions, mode="back_ratio")
    front = adjust_prices(bars, actions, mode="front")
    back = adjust_prices(bars, actions, mode="back")

    assert bars.equals(original)
    assert none["close"].tolist() == [10.0, 9.0, 9.5]
    assert front_ratio["close"].tolist() == [5.0, 9.0, 9.5]
    assert back_ratio["close"].tolist() == [10.0, 18.0, 19.0]
    assert front.loc[0, "close"] == pytest.approx((10.0 - 1.0) / 1.1)
    assert front.loc[1, "close"] == 9.0
    assert back.loc[0, "close"] == 10.0
    assert back.loc[1, "close"] == pytest.approx(10.9)
    for adjusted in (front_ratio, back_ratio, front, back):
        assert adjusted["volume"].tolist() == [100, 200, 300]
        assert adjusted["amount"].tolist() == [1000.0, 1800.0, 2850.0]


def test_five_minute_aggregation_never_crosses_lunch() -> None:
    frame = pd.DataFrame(
        [
            stored_bar("600000.SH", "20260717112900", 10.0).sqlite_values(),
            stored_bar("600000.SH", "20260717113000", 10.1).sqlite_values(),
            stored_bar("600000.SH", "20260717130000", 10.2).sqlite_values(),
            stored_bar("600000.SH", "20260717130100", 10.3).sqlite_values(),
        ],
        columns=[
            "symbol",
            "ts",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "settlement_price",
            "open_interest",
            "pre_close",
            "suspend_flag",
            "source",
            "ingested_at",
            "source_hash",
        ],
    )
    aggregated = aggregate_bars(frame, "5m")
    assert aggregated["volume"].tolist() == [200, 200]
    local_times = pd.to_datetime(aggregated["ts"], unit="ms", utc=True).dt.tz_convert(CHINA_TZ)
    assert local_times.dt.strftime("%H:%M").tolist() == ["11:30", "13:01"]


def test_local_xtdata_contract_reads_sqlite_and_enforces_count(tmp_path: Path) -> None:
    settings = DataPlatformSettings(storage=StorageConfig(root=tmp_path))
    catalog = CatalogRepository(settings.storage)
    catalog.initialize()
    catalog.upsert_calendar("SH", [20260715, 20260716, 20260717], source="test")
    catalog.upsert_actions(
        [CorporateActionRecord(symbol="600000.SH", action_date=20260716, dr=2.0)]
    )
    bars = BarRepository(settings.storage)
    bars.upsert(
        "1d",
        [
            stored_bar("600000.SH", "20260715000000", 10.0),
            stored_bar("600000.SH", "20260716000000", 10.5),
            stored_bar("600000.SH", "20260717000000", 11.0),
        ],
    )
    local = LocalXtData(settings)

    by_symbol = local.get_market_data_ex(
        field_list=["close", "volume"],
        stock_list=["600000.SH"],
        period="1d",
        start_time="20260715",
        end_time="20260718",
        count=2,
        dividend_type="front_ratio",
        fill_data=False,
    )
    by_field = local.get_market_data(
        field_list=["close"],
        stock_list=["600000.SH"],
        period="1d",
        start_time="20260715",
        end_time="20260718",
        count=-1,
        dividend_type="none",
        fill_data=False,
    )

    assert list(by_symbol) == ["600000.SH"]
    assert by_symbol["600000.SH"]["close"].tolist() == [10.5, 11.0]
    assert by_symbol["600000.SH"].index.name == "stime"
    assert by_field["close"].index.tolist() == ["600000.SH"]
    assert by_field["close"].shape == (1, 3)
