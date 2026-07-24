from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

import pandas as pd

from dyadtetrade.db.parquet_store import DataLayer, ParquetMetadata, ParquetStore
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.market_data.normalizer import TICK_FIELDS
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline

MARKET_FIELDS = ["open", "high", "low", "close", "volume", "amount"]


class MarketDataService:
    def __init__(
        self,
        gateway: XtQuantGateway | None = None,
        store: ParquetStore | None = None,
    ) -> None:
        self.gateway = gateway or XtQuantGateway()
        self.store = store or ParquetStore()
        self.pipeline = MarketDataPipeline(self.store)

    def download_and_store(
        self,
        *,
        symbols: list[str],
        start_date: str,
        end_date: str,
        period: str,
        adjust: str,
        incrementally: bool,
    ) -> tuple[list[ParquetMetadata], dict[str, str]]:
        files: list[ParquetMetadata] = []
        errors: dict[str, str] = {}

        for symbol in symbols:
            try:
                self.gateway.download_history(
                    symbol,
                    period=period,
                    start_time=start_date,
                    end_time=end_date,
                    incrementally=incrementally,
                )
                frames = self.gateway.local_bars(
                    [symbol],
                    fields=TICK_FIELDS if period == "tick" else MARKET_FIELDS,
                    period=period,
                    start_time=start_date,
                    end_time=end_date,
                    dividend_type="none" if period == "tick" else adjust,
                    fill_data=False,
                )
                raw = frames.get(symbol)
                if raw is None or raw.empty:
                    raise ValueError("miniQMT 返回空行情")

                result = self.pipeline.process(
                    raw,
                    symbol=symbol,
                    period=period,
                    adjust=adjust,
                )
                files.extend(result.files)
            except Exception as exc:
                errors[symbol] = str(exc)
        return files, errors

    def read_stored(
        self,
        *,
        symbol: str,
        period: str,
        adjust: str,
        layer: DataLayer,
        limit: int,
    ) -> list[dict[str, Any]]:
        frame = self.store.read(symbol, period, adjust, layer)
        frame = frame.tail(limit).copy()
        if "datetime" in frame:
            frame["datetime"] = pd.to_datetime(frame["datetime"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        return cast(list[dict[str, Any]], frame.to_dict(orient="records"))


def metadata_to_dict(metadata: ParquetMetadata) -> dict[str, Any]:
    value = asdict(metadata)
    value["file_path"] = str(metadata.file_path)
    return value
