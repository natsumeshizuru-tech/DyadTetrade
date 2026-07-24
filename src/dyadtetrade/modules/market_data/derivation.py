from __future__ import annotations

from dataclasses import dataclass

from dyadtetrade.db.parquet_store import ParquetMetadata, ParquetStore
from dyadtetrade.modules.market_data.periods import derivation_for, resample_ohlcv
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline


@dataclass(frozen=True)
class DerivationResult:
    symbol: str
    source_period: str
    target_period: str
    rows: int
    files: list[ParquetMetadata]


class DerivedPeriodBuilder:
    def __init__(self, store: ParquetStore | None = None) -> None:
        self.store = store or ParquetStore()
        self.pipeline = MarketDataPipeline(self.store)

    def derive(self, *, symbol: str, target_period: str, adjust: str) -> DerivationResult:
        spec = derivation_for(target_period)
        source = self.store.read(symbol, spec.source_period, adjust, "trusted")
        derived = resample_ohlcv(source, target_period=target_period)
        if derived.empty:
            raise ValueError(f"{symbol} {target_period} 派生结果为空")
        result = self.pipeline.process(
            derived,
            symbol=symbol,
            period=target_period,
            adjust=adjust,
            imported_from=f"derived:{spec.source_period}:{adjust}",
        )
        return DerivationResult(
            symbol=symbol,
            source_period=spec.source_period,
            target_period=target_period,
            rows=result.trusted_rows,
            files=result.files,
        )
