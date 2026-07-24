from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pandas as pd

from dyadtetrade.core.config import get_settings

DataLayer = Literal["raw", "clean", "trusted", "bt_ready"]


@dataclass(frozen=True)
class ParquetMetadata:
    symbol: str
    period: str
    adjust: str
    layer: DataLayer
    file_path: Path
    start_date: str | None
    end_date: str | None
    rows: int
    file_size: int
    checksum: str
    schema_version: str = "1"
    pipeline_version: str = "market-v1"
    imported_from: str | None = None


class ParquetStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().data_root

    def path_for(self, symbol: str, period: str, adjust: str, layer: DataLayer) -> Path:
        safe_symbol = symbol.replace(".", "_")
        return (
            self.root
            / layer
            / f"bars_{period}"
            / f"symbol={symbol}"
            / f"{safe_symbol}_{period}_{adjust}.parquet"
        )

    def write(
        self,
        frame: pd.DataFrame,
        *,
        symbol: str,
        period: str,
        adjust: str,
        layer: DataLayer = "trusted",
        merge: bool = False,
        imported_from: str | None = None,
    ) -> ParquetMetadata:
        path = self.path_for(symbol, period, adjust, layer)
        path.parent.mkdir(parents=True, exist_ok=True)
        output = frame.copy()
        if merge and path.exists():
            existing = pd.read_parquet(path)
            output = pd.concat([existing, output], ignore_index=True)
            keys = [key for key in ("datetime", "symbol") if key in output.columns]
            if keys:
                output = output.drop_duplicates(subset=keys, keep="last")
            if "datetime" in output:
                output = output.sort_values("datetime").reset_index(drop=True)
        temp_path = path.with_suffix(".tmp.parquet")
        output.to_parquet(
            temp_path,
            index=False,
            engine="pyarrow",
            compression="zstd",
        )
        temp_path.replace(path)

        start_date = None
        end_date = None
        if not output.empty and "datetime" in output.columns:
            dates = pd.to_datetime(output["datetime"])
            start_date = dates.min().strftime("%Y%m%d")
            end_date = dates.max().strftime("%Y%m%d")

        return ParquetMetadata(
            symbol=symbol,
            period=period,
            adjust=adjust,
            layer=layer,
            file_path=path.resolve(),
            start_date=start_date,
            end_date=end_date,
            rows=len(output),
            file_size=path.stat().st_size,
            checksum=sha256(path.read_bytes()).hexdigest(),
            schema_version="2" if period == "tick" else "1",
            pipeline_version="tick-l1-3s-v1" if period == "tick" else "market-v1",
            imported_from=imported_from,
        )

    def read(self, symbol: str, period: str, adjust: str, layer: DataLayer) -> pd.DataFrame:
        path = self.path_for(symbol, period, adjust, layer)
        if not path.exists():
            raise FileNotFoundError(path)
        return pd.read_parquet(path)
