from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dyadtetrade.core.symbols import infer_asset_type
from dyadtetrade.db.parquet_store import ParquetMetadata, ParquetStore
from dyadtetrade.modules.market_data.normalizer import (
    normalize_ohlcv,
    normalize_tick,
    normalize_tick_datetime,
    raw_to_tabular,
)
from dyadtetrade.modules.market_data.quality import (
    QualityFinding,
    inspect_bar_quality,
    inspect_tick_quality,
)


@dataclass(frozen=True)
class PipelineResult:
    files: list[ParquetMetadata]
    findings: list[QualityFinding]
    trusted_rows: int


class MarketDataPipeline:
    def __init__(self, store: ParquetStore) -> None:
        self.store = store

    def process(
        self,
        raw: pd.DataFrame,
        *,
        symbol: str,
        period: str,
        adjust: str,
        expected_days: list[str] | None = None,
        imported_from: str | None = None,
        asset_type: str | None = None,
    ) -> PipelineResult:
        resolved_asset_type = asset_type or infer_asset_type(symbol)
        raw_frame = raw_to_tabular(raw, symbol)
        if period == "tick":
            return self._process_tick(
                raw_frame,
                raw,
                symbol=symbol,
                expected_days=expected_days,
                imported_from=imported_from,
            )
        raw_findings = inspect_bar_quality(
            raw_frame,
            symbol=symbol,
            expected_days=expected_days,
            asset_type=resolved_asset_type,
        )
        clean = normalize_ohlcv(
            raw,
            symbol=symbol,
            period=period,
            adjust=adjust,
            asset_type=resolved_asset_type,
        )
        clean_findings = inspect_bar_quality(
            clean,
            symbol=symbol,
            expected_days=expected_days,
            asset_type=resolved_asset_type,
        )
        findings = _unique_findings([*raw_findings, *clean_findings])

        valid = _valid_rows(clean)
        if valid.empty:
            raise ValueError("质量检查后无可用行情行")
        bt_ready = valid.sort_values("datetime").reset_index(drop=True)

        layers = (("raw", raw_frame), ("clean", clean), ("trusted", valid), ("bt_ready", bt_ready))
        files = [
            self.store.write(
                frame,
                symbol=symbol,
                period=period,
                adjust=adjust,
                layer=layer,  # type: ignore[arg-type]
                merge=True,
                imported_from=imported_from,
            )
            for layer, frame in layers
        ]
        return PipelineResult(files=files, findings=findings, trusted_rows=len(valid))

    def _process_tick(
        self,
        raw_frame: pd.DataFrame,
        source: pd.DataFrame,
        *,
        symbol: str,
        expected_days: list[str] | None,
        imported_from: str | None,
    ) -> PipelineResult:
        clean = normalize_tick(source, symbol=symbol, expected_days=expected_days)
        raw_storage = raw_frame.copy()
        if "datetime" not in raw_storage:
            time_column = next((name for name in ("time", "timetag") if name in raw_storage), None)
            if time_column is not None:
                raw_storage = raw_storage.rename(columns={time_column: "datetime"})
        if "datetime" in raw_storage:
            raw_storage["datetime"] = normalize_tick_datetime(raw_storage["datetime"])
        findings = _unique_findings(
            [
                *inspect_tick_quality(raw_frame, symbol=symbol, expected_days=expected_days),
                *inspect_tick_quality(clean, symbol=symbol, expected_days=expected_days),
            ]
        )
        # Tick volume makes four physical copies unsafe. Raw is the vendor-shaped
        # source; trusted is the normalized canonical table. bt_ready is not a tick
        # concern and clean would duplicate trusted bytes without adding lineage.
        files = [
            self.store.write(
                raw_storage,
                symbol=symbol,
                period="tick",
                adjust="none",
                layer="raw",
                merge=True,
                imported_from=imported_from,
            ),
            self.store.write(
                clean,
                symbol=symbol,
                period="tick",
                adjust="none",
                layer="trusted",
                merge=True,
                imported_from=imported_from,
            ),
        ]
        return PipelineResult(files=files, findings=findings, trusted_rows=len(clean))


def _valid_rows(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    numeric = result[["open", "high", "low", "close", "volume", "amount"]]
    mask = numeric.notna().all(axis=1)
    mask &= (numeric[["open", "high", "low", "close"]] > 0).all(axis=1)
    mask &= result["high"] >= result[["open", "low", "close"]].max(axis=1)
    mask &= result["low"] <= result[["open", "high", "close"]].min(axis=1)
    mask &= (result[["volume", "amount"]] >= 0).all(axis=1)
    return result.loc[mask].drop_duplicates(subset=["datetime", "symbol"], keep="last")


def _unique_findings(findings: list[QualityFinding]) -> list[QualityFinding]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[QualityFinding] = []
    for item in findings:
        key = (item.symbol, item.check_code, item.trade_date)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
