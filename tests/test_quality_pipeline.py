from pathlib import Path

import duckdb
import pandas as pd

from dyadtetrade.db.duckdb_store import refresh_market_views
from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline
from dyadtetrade.modules.market_data.quality import inspect_bar_quality, write_quality_report


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [10.0, 10.2],
            "high": [10.5, 10.8],
            "low": [9.8, 10.1],
            "close": [10.2, 10.6],
            "volume": [1000, 0],
            "amount": [10200, 0],
        },
        index=["20260709", "20260710"],
    )


def test_quality_finds_duplicate_invalid_ohlc_and_missing_day(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "datetime": ["20260708", "20260708", "20260710"],
            "open": [10, 10, 10],
            "high": [11, 9, 11],
            "low": [9, 8, 9],
            "close": [10, 10, 10],
            "volume": [1, -1, 1],
            "amount": [10, 10, 10],
        }
    )
    findings = inspect_bar_quality(
        frame, symbol="600519.SH", expected_days=["20260708", "20260709", "20260710"]
    )
    codes = {item.check_code for item in findings}
    assert {"duplicate_rows", "invalid_ohlc", "negative_volume_amount"} <= codes
    assert "missing_trading_days" in codes
    report = write_quality_report(findings, report_root=tmp_path, report_name="quality")
    assert report.csv_path.exists() and report.json_path.exists()


def test_pipeline_is_idempotent_and_creates_duckdb_views(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    pipeline = MarketDataPipeline(store)
    first = pipeline.process(sample_frame(), symbol="600519.SH", period="1d", adjust="front")
    second = pipeline.process(sample_frame(), symbol="600519.SH", period="1d", adjust="front")
    assert {item.layer for item in first.files} == {"raw", "clean", "trusted", "bt_ready"}
    assert all(item.rows == 2 for item in second.files)
    trusted = store.read("600519.SH", "1d", "front", "trusted")
    assert bool(trusted.loc[1, "suspended"])

    connection = duckdb.connect(":memory:")
    try:
        refresh_market_views(connection=connection, data_root=tmp_path)
        assert connection.execute("select count(*) from bars_trusted").fetchone() == (2,)
    finally:
        connection.close()


def test_index_turnover_sentinel_keeps_price_history_usable(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [-1, -1],
            "amount": [100000.0, 101000.0],
        },
        index=["20260709", "20260710"],
    )

    result = MarketDataPipeline(ParquetStore(tmp_path)).process(
        frame,
        symbol="399379.SZ",
        period="1d",
        adjust="front",
        asset_type="index",
    )
    trusted = pd.read_parquet(
        next(item.file_path for item in result.files if item.layer == "trusted")
    )

    assert result.trusted_rows == 2
    assert set(trusted["quality_status"]) == {"price_only"}
    assert (trusted["volume"] == 0).all()
    assert trusted["amount"].tolist() == [100000.0, 101000.0]
    finding = next(
        item for item in result.findings if item.check_code == "index_volume_amount_unavailable"
    )
    assert finding.severity == "warning"
