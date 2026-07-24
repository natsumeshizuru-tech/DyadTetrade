from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.modules.market_data.adjustment_validation import compare_adjusted_data
from dyadtetrade.modules.market_data.bulk_plan import (
    create_download_batches,
    estimate_capacity,
)
from dyadtetrade.modules.market_data.derivation import DerivedPeriodBuilder
from dyadtetrade.modules.market_data.periods import resample_ohlcv
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline


def minute_frame() -> pd.DataFrame:
    times = pd.to_datetime(
        [
            "2026-07-10 09:31",
            "2026-07-10 09:32",
            "2026-07-10 09:35",
            "2026-07-10 13:01",
            "2026-07-10 13:02",
        ]
    )
    return pd.DataFrame(
        {
            "datetime": times,
            "symbol": "600519.SH",
            "open": [10.0, 10.1, 10.2, 10.4, 10.5],
            "high": [10.2, 10.3, 10.4, 10.6, 10.7],
            "low": [9.9, 10.0, 10.1, 10.3, 10.4],
            "close": [10.1, 10.2, 10.3, 10.5, 10.6],
            "volume": [1, 2, 3, 4, 5],
            "amount": [10, 20, 30, 40, 50],
            "pre_close": [9.8, 9.8, 9.8, 9.8, 9.8],
            "suspended": False,
            "adjust_factor": 1.0,
            "period": "1m",
            "adjust": "none",
        }
    )


def test_resample_intraday_does_not_fill_lunch_break() -> None:
    result = resample_ohlcv(minute_frame(), target_period="5m")
    assert len(result) == 3
    assert result["volume"].tolist() == [3, 3, 9]
    assert result["datetime"].tolist() == list(
        pd.to_datetime(["2026-07-10 09:32", "2026-07-10 09:35", "2026-07-10 13:02"])
    )


def test_week_quarter_year_use_last_real_trading_day() -> None:
    source = pd.concat(
        [
            minute_frame().iloc[[0]].assign(datetime=pd.Timestamp("2026-03-30"), period="1d"),
            minute_frame().iloc[[0]].assign(datetime=pd.Timestamp("2026-03-31"), period="1d"),
            minute_frame().iloc[[0]].assign(datetime=pd.Timestamp("2026-04-01"), period="1d"),
        ],
        ignore_index=True,
    )
    quarter = resample_ohlcv(source, target_period="1q")
    year = resample_ohlcv(source, target_period="1y")
    assert quarter["datetime"].tolist() == list(pd.to_datetime(["2026-03-31", "2026-04-01"]))
    assert year["datetime"].tolist() == [pd.Timestamp("2026-04-01")]


def test_derived_builder_is_idempotent_and_writes_all_bar_layers(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    pipeline = MarketDataPipeline(store)
    source = minute_frame().set_index("datetime").drop(columns=["symbol", "period", "adjust"])
    pipeline.process(source, symbol="600519.SH", period="1m", adjust="none")
    builder = DerivedPeriodBuilder(store)
    first = builder.derive(symbol="600519.SH", target_period="5m", adjust="none")
    second = builder.derive(symbol="600519.SH", target_period="5m", adjust="none")
    assert first.rows == second.rows == 3
    assert {item.layer for item in second.files} == {"raw", "clean", "trusted", "bt_ready"}


def test_adjustment_check_detects_consistent_ratio_and_volume_mismatch() -> None:
    raw = minute_frame()
    adjusted = raw.copy()
    adjusted[["open", "high", "low", "close"]] *= 0.5
    good = compare_adjusted_data(raw, adjusted, symbol="600519.SH", period="1m")
    assert good.passed
    adjusted[["open", "high", "low", "close"]] += 3.25
    affine = compare_adjusted_data(raw, adjusted, symbol="600519.SH", period="1m")
    assert affine.passed
    adjusted.loc[0, "volume"] += 1
    bad = compare_adjusted_data(raw, adjusted, symbol="600519.SH", period="1m")
    assert not bad.passed and bad.volume_mismatch_rows == 1


def test_tick_pipeline_has_raw_and_trusted_only_and_is_idempotent(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "time": [1720575000000, 1720575003000],
            "lastPrice": [10.0, 10.1],
            "open": [9.9, 9.9],
            "high": [10.0, 10.1],
            "low": [9.8, 9.8],
            "lastClose": [9.8, 9.8],
            "volume": [100, 120],
            "amount": [1000, 1212],
        }
    )
    pipeline = MarketDataPipeline(ParquetStore(tmp_path))
    first = pipeline.process(frame, symbol="600519.SH", period="tick", adjust="none")
    second = pipeline.process(frame, symbol="600519.SH", period="tick", adjust="none")
    assert {item.layer for item in first.files} == {"raw", "trusted"}
    assert all(item.rows == 2 for item in second.files)
    assert {item.start_date for item in second.files} == {"20240710"}
    assert {item.pipeline_version for item in second.files} == {"tick-l1-3s-v1"}
    raw = pd.read_parquet(next(item.file_path for item in second.files if item.layer == "raw"))
    assert raw["datetime"].dt.year.unique().tolist() == [2024]
    assert raw["datetime"].dt.hour.unique().tolist() == [9]
    trusted_path = next(item.file_path for item in second.files if item.layer == "trusted")
    trusted = pd.read_parquet(trusted_path)
    assert trusted["source_level"].unique().tolist() == ["level1"]
    assert trusted["cadence_seconds"].unique().tolist() == [3]
    row_group = pq.ParquetFile(trusted_path).metadata.row_group(0)
    assert {row_group.column(index).compression for index in range(row_group.num_columns)} == {
        "ZSTD"
    }


def test_capacity_gate_rejects_unbounded_full_market_tick(tmp_path: Path) -> None:
    estimate = estimate_capacity(
        5000,
        data_root=tmp_path,
        as_of=date(2026, 7, 13),
        include_tick=True,
    )
    assert not estimate.bounded
    assert estimate.by_dataset["tick"] > 1_000_000_000_000
    assert any("Tick" in reason for reason in estimate.reasons)


def test_plan_uses_native_base_periods_and_requires_explicit_tick() -> None:
    without_tick = create_download_batches(
        ["600519.SH", "000001.SZ"], end_date="20260710", batch_size=1
    )
    with_tick = create_download_batches(["600519.SH"], end_date="20260710", include_tick=True)
    assert {item.period for item in without_tick} == {"1d", "1m"}
    assert {item.adjust for item in without_tick} == {"none", "front"}
    assert any(item.period == "tick" for item in with_tick)
    tick_windows = [(item.start_date, item.end_date) for item in with_tick if item.period == "tick"]
    assert tick_windows == [
        ("20240101", "20241231"),
        ("20250101", "20251231"),
        ("20260101", "20260710"),
    ]
