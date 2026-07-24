from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from dyadtetrade.data.config import (
    BackfillConfig,
    DataPlatformSettings,
    StorageConfig,
    UniverseConfig,
)
from dyadtetrade.data.downloader import (
    BackfillRunner,
    sanitize_xtdata_calendar_rows,
    sanitize_xtdata_flow_fields,
)
from dyadtetrade.data.gateway import DataGatewayUnavailable
from dyadtetrade.data.models import BarRecord, InstrumentRecord
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository
from dyadtetrade.data.xtdata_gateway import XtDataGateway


class FakeGateway:
    def __init__(self) -> None:
        self.download_calls: list[tuple[tuple[str, ...], str, str, str]] = []

    def doctor(self) -> dict[str, Any]:
        return {"safe_read_only": True}

    def download_holiday_data(self) -> None:
        return None

    def get_trading_calendar(self, market: str, start: str, end: str) -> list[str]:
        del market, start, end
        return ["20260715", "20260716", "20260717"]

    def refresh_sector_data(self) -> None:
        return None

    def refresh_history_contracts(self) -> None:
        return None

    def get_sector_list(self) -> list[str]:
        return ["沪深京A股", "过期股票"]

    def get_stock_list_in_sector(self, sector: str) -> list[str]:
        return {
            "沪深京A股": ["600000.SH"],
            "过期股票": ["000001.SZ"],
        }[sector]

    def get_instrument_detail(self, symbol: str) -> dict[str, Any]:
        return {
            "InstrumentName": symbol,
            "OpenDate": 19990101,
            "ExpireDate": 20260716 if symbol == "000001.SZ" else 0,
            "PriceTick": 0.01,
            "VolumeMultiple": 1,
        }

    def get_instrument_details(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        return {symbol: self.get_instrument_detail(symbol) for symbol in symbols}

    def download_history(self, symbols: list[str], *, period: str, start: str, end: str) -> None:
        self.download_calls.append((tuple(symbols), period, start, end))

    def get_local_data(
        self,
        symbols: list[str],
        *,
        fields: list[str],
        period: str,
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        del fields, period, start, end
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            days = (
                ["20260715", "20260716"]
                if symbol == "000001.SZ"
                else [
                    "20260715",
                    "20260717",
                ]
            )
            result[symbol] = pd.DataFrame(
                {
                    "open": [10.0, 10.1],
                    "high": [10.2, 10.3],
                    "low": [9.9, 10.0],
                    "close": [10.1, 10.2],
                    "volume": [1000, 1100],
                    "amount": [10100.0, 11220.0],
                },
                index=days,
            )
        return result

    def get_divid_factors(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        del symbol, start, end
        return pd.DataFrame()

    def get_divid_factors_batch(
        self, symbols: list[str], start: str, end: str
    ) -> dict[str, pd.DataFrame]:
        return {symbol: self.get_divid_factors(symbol, start, end) for symbol in symbols}


def test_vendor_signed_overflow_flow_is_quarantined_without_losing_prices() -> None:
    source = BarRecord(
        symbol="000011.SH",
        ts=1,
        trade_date=20240930,
        open=6618.309,
        high=6901.128,
        low=6455.338,
        close=6897.187,
        volume=-1_802_976_519,
        amount=326_299_781_257.0,
    )

    cleaned, issues = sanitize_xtdata_flow_fields([source], period="1d", job_id="job")

    assert cleaned[0].close == source.close
    assert cleaned[0].volume is None
    assert cleaned[0].amount == source.amount
    assert [(issue.severity, issue.rule) for issue in issues] == [
        ("warning", "negative_volume_quarantined")
    ]


def test_closed_session_row_is_quarantined_without_losing_valid_rows() -> None:
    weekend = BarRecord(
        symbol="000974.SH",
        ts=1,
        trade_date=20130803,
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        volume=100,
        amount=1000.0,
    )
    valid = BarRecord(
        symbol="000974.SH",
        ts=2,
        trade_date=20130805,
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        volume=100,
        amount=1000.0,
    )

    cleaned, issues = sanitize_xtdata_calendar_rows(
        [weekend, valid], period="1d", open_days={20130805}, job_id="job"
    )

    assert cleaned == [valid]
    assert [(issue.severity, issue.rule, issue.trade_date) for issue in issues] == [
        ("warning", "closed_session_row_quarantined", 20130803)
    ]
    assert issues[0].details["raw_bar"]["close"] == 10.0


class OneFailureGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def download_history(self, symbols: list[str], *, period: str, start: str, end: str) -> None:
        if not self.failed and "000001.SZ" in symbols:
            self.failed = True
            raise DataGatewayUnavailable("transient batch failure")
        super().download_history(symbols, period=period, start=start, end=end)


class RetryGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.allow_sz = False

    def download_history(self, symbols: list[str], *, period: str, start: str, end: str) -> None:
        if "000001.SZ" in symbols and not self.allow_sz:
            raise DataGatewayUnavailable("simulated interrupted symbol")
        super().download_history(symbols, period=period, start=start, end=end)


def settings(tmp_path: Path) -> DataPlatformSettings:
    return DataPlatformSettings(
        storage=StorageConfig(root=tmp_path / "sqlite", minimum_free_gib=1),
        universe=UniverseConfig(sectors=["沪深京A股"], include_delisted=True),
        backfill=BackfillConfig(
            start_date="20260715",
            requested_end_date="20260718",
            periods=["1d"],
            download_batch_symbols=50,
            read_batch_symbols=20,
        ),
    )


def test_empty_index_contract_is_recognized_for_upstream_evidence(tmp_path: Path) -> None:
    runner = BackfillRunner(settings(tmp_path), FakeGateway())
    runner.catalog.initialize()
    runner.catalog.upsert_instruments(
        [
            InstrumentRecord(
                symbol="395005.SZ",
                code="395005",
                market="SZ",
                name="主板DR",
                instrument_type="index",
                list_date=None,
                delist_date=99999999,
                is_active=True,
                price_tick=0.001,
                volume_multiple=1.0,
                raw_json="{}",
            )
        ]
    )

    assert runner._is_index_symbol("395005.SZ")
    assert not runner._is_index_symbol("000001.SZ")


def test_backfill_clips_calendar_includes_delisted_and_is_idempotent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    configured = settings(tmp_path)
    gateway = FakeGateway()
    runner = BackfillRunner(configured, gateway)

    first = runner.run(
        start=20260715,
        end=20260718,
        periods=["1d"],
        sectors=["沪深京A股"],
        include_delisted=True,
    )
    second = runner.run(
        start=20260715,
        end=20260718,
        periods=["1d"],
        sectors=["沪深京A股"],
        include_delisted=True,
    )

    assert first.requested_end == 20260718
    assert first.effective_end == 20260717
    assert first.symbol_count == 2
    assert first.delisted_or_historical_count == 1
    assert first.inserted_rows == 4
    assert second.inserted_rows == 0
    assert second.unchanged_rows == 4
    repository = BarRepository(configured.storage)
    assert repository.symbol_stats("1d", "600000.SH")[2] == 2
    assert repository.symbol_stats("1d", "000001.SZ")[2] == 2
    with sqlite3.connect(configured.storage.root / "catalog.db") as connection:
        assert connection.execute(
            "SELECT requested_end, effective_end FROM job_runs ORDER BY started_at LIMIT 1"
        ).fetchone() == (20260718, 20260717)
        assert connection.execute(
            "SELECT COUNT(*) FROM sector_snapshots WHERE sector_name='过期股票'"
        ).fetchone() == (1,)


def test_batch_failure_falls_back_to_isolated_symbols(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    runner = BackfillRunner(settings(tmp_path), OneFailureGateway())
    summary = runner.run(
        start=20260715,
        end=20260718,
        periods=["1d"],
        sectors=["沪深京A股"],
        include_delisted=True,
    )
    assert summary.failed_items == 0
    assert summary.inserted_rows == 4


def test_interrupted_symbol_is_filled_on_next_run(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    gateway = RetryGateway()
    configured = settings(tmp_path)
    runner = BackfillRunner(configured, gateway)
    first = runner.run(
        start=20260715,
        end=20260718,
        periods=["1d"],
        sectors=configured.universe.sectors,
        include_delisted=True,
    )
    gateway.allow_sz = True
    second = runner.run(
        start=20260715,
        end=20260718,
        periods=["1d"],
        sectors=configured.universe.sectors,
        include_delisted=True,
    )

    assert first.failed_items == 1
    assert first.inserted_rows == 2
    assert second.failed_items == 0
    assert second.inserted_rows == 2
    assert BarRepository(runner.settings.storage).symbol_stats("1d", "000001.SZ")[2] == 2


def test_resume_window_batches_missing_prefixes_and_keeps_three_day_overlap(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    runner = BackfillRunner(configured, FakeGateway())
    runner.initialize()
    catalog = CatalogRepository(configured.storage)
    catalog.upsert_instruments(
        [
            InstrumentRecord(
                symbol="600000.SH",
                code="600000",
                market="SH",
                name="sample",
                instrument_type="stock",
                list_date=20260714,
                delist_date=None,
                is_active=True,
                price_tick=0.01,
                volume_multiple=1,
                raw_json=None,
            ),
            InstrumentRecord(
                symbol="000001.SZ",
                code="000001",
                market="SZ",
                name="lifecycle sample",
                instrument_type="stock",
                list_date=20260716,
                delist_date=20260717,
                is_active=False,
                price_tick=0.01,
                volume_multiple=1,
                raw_json=None,
            ),
        ]
    )
    trading_days = [20260714, 20260715, 20260716, 20260717, 20260718, 20260720]
    bars = [
        BarRecord(
            symbol="600000.SH",
            ts=day,
            trade_date=day,
            open=10.0,
            high=10.0,
            low=10.0,
            close=10.0,
            volume=100,
            amount=1000.0,
            source_hash=str(day),
        )
        for day in trading_days[:5]
    ]
    runner.bars.upsert("1d", bars)

    assert runner._resume_window(
        "600000.SH",
        period="1d",
        start=20260714,
        end=20260720,
        trading_days=trading_days,
    ) == (20260716, 20260720)
    assert runner._resume_window(
        "000001.SZ",
        period="1d",
        start=20260714,
        end=20260720,
        trading_days=trading_days,
    ) == (20260714, 20260720)

    assert (
        runner._resume_window(
            "000001.SZ",
            period="1d",
            start=20260714,
            end=20260720,
            trading_days=trading_days,
            stored_stats=(None, None, 0),
            scope_completed=True,
        )
        is None
    )

    runner.bars.upsert(
        "1d",
        [
            BarRecord(
                symbol="000001.SZ",
                ts=20260717,
                trade_date=20260717,
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                volume=100,
                amount=1000.0,
                source_hash="late-prefix",
            )
        ],
    )
    assert runner._resume_window(
        "000001.SZ",
        period="1d",
        start=20260714,
        end=20260720,
        trading_days=trading_days,
    ) == (20260714, 20260720)
    assert runner._resume_window(
        "000001.SZ",
        period="1d",
        start=20260714,
        end=20260720,
        trading_days=trading_days,
        scope_completed=True,
    ) == (20260716, 20260717)


class FakeXtDataModule:
    __file__ = "fake/xtdata.py"
    data_dir = "fake-data"

    def get_sector_list(self) -> list[str]:
        return ["沪深京A股"]

    def get_local_data(self, **kwargs: Any) -> dict[str, pd.DataFrame]:
        assert kwargs["dividend_type"] == "none"
        assert kwargs["fill_data"] is False
        return {"600000.SH": pd.DataFrame({"close": [10.0]}, index=["20260717"])}


def test_xtdata_gateway_forces_raw_unfilled_reads() -> None:
    gateway = XtDataGateway(settings(Path("unused")).qmt, module=FakeXtDataModule())
    result = gateway.get_local_data(
        ["600000.SH"],
        fields=["close"],
        period="1d",
        start="20260717",
        end="20260717",
    )
    assert result["600000.SH"]["close"].tolist() == [10.0]


def test_data_package_has_no_live_order_calls() -> None:
    root = Path(__file__).parents[1] / "src" / "dyadtetrade" / "data"
    forbidden = ("order_stock(", "order_stock_async(", "cancel_order_stock(")
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        assert all(token not in source for token in forbidden), path
