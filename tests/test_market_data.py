import warnings
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.market_data.normalizer import normalize_datetime, normalize_ohlcv
from dyadtetrade.modules.market_data.service import MarketDataService


class FakeXtData:
    def get_sector_list(self) -> list[str]:
        return ["沪深京A股", "沪深A股"]

    def get_stock_list_in_sector(self, sector: str) -> list[str]:
        assert sector in {"沪深京A股", "沪深A股"}
        return ["600519.SH", "000001.SZ", "430047.BJ", "510300.SH"]

    def get_trading_calendar(self, market: str, start: str, end: str) -> list[str]:
        assert market == "SH"
        assert start <= end
        return ["20260709", "20260710"]

    def download_history_data(self, **_: Any) -> None:
        return None

    def get_market_data_ex(self, **kwargs: Any) -> dict[str, pd.DataFrame]:
        symbol = kwargs["stock_list"][0]
        frame = pd.DataFrame(
            {
                "open": [10.0, 10.2],
                "high": [10.5, 10.8],
                "low": [9.8, 10.1],
                "close": [10.2, 10.6],
                "volume": [1000, 1200],
                "amount": [10200, 12720],
            },
            index=["20260709", "20260710"],
        )
        return {symbol: frame}


def test_gateway_filters_a_share_universe() -> None:
    gateway = XtQuantGateway(FakeXtData())
    assert gateway.list_a_share_universe() == ["000001.SZ", "430047.BJ", "600519.SH"]
    assert gateway.trading_days("20260701", "20260711") == ["20260709", "20260710"]


def test_normalizer_rejects_invalid_high() -> None:
    frame = pd.DataFrame(
        {"open": [10], "high": [9], "low": [8], "close": [10]},
        index=["20260710"],
    )
    with pytest.raises(ValueError, match="high"):
        normalize_ohlcv(frame, symbol="600519.SH", period="1d", adjust="front")


def test_normalizer_drops_zero_price_suspension_rows() -> None:
    frame = pd.DataFrame(
        {
            "open": [0, 10],
            "high": [0, 11],
            "low": [0, 9],
            "close": [0, 10.5],
            "volume": [0, 100],
            "amount": [0, 1050],
        },
        index=["20260709", "20260710"],
    )
    result = normalize_ohlcv(frame, symbol="600519.SH", period="1d", adjust="front")
    assert len(result) == 1
    assert result.iloc[0]["close"] == 10.5


def test_normalize_datetime_preserves_timezone_without_dtype_warning() -> None:
    values = [
        datetime(2026, 7, 13, 9, 35, tzinfo=ZoneInfo("Asia/Shanghai")),
        datetime(2026, 7, 13, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        result = normalize_datetime(values)

    assert [item.isoformat() for item in result] == [
        "2026-07-13T09:35:00+08:00",
        "2026-07-13T09:40:00+08:00",
    ]


def test_service_writes_four_data_layers(tmp_path: Path) -> None:
    service = MarketDataService(
        gateway=XtQuantGateway(FakeXtData()),
        store=ParquetStore(tmp_path),
    )
    files, errors = service.download_and_store(
        symbols=["600519.SH"],
        start_date="20260709",
        end_date="20260710",
        period="1d",
        adjust="front",
        incrementally=True,
    )

    assert errors == {}
    assert {item.layer for item in files} == {"raw", "clean", "trusted", "bt_ready"}
    assert all(item.rows == 2 and item.file_path.exists() for item in files)
    records = service.read_stored(
        symbol="600519.SH",
        period="1d",
        adjust="front",
        layer="trusted",
        limit=1,
    )
    assert len(records) == 1
    assert records[0]["close"] == 10.6
