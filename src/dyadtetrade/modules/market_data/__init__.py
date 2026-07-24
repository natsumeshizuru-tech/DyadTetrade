"""Market data ingestion, normalization and storage."""

from dyadtetrade.modules.market_data.adjustment_validation import (
    AdjustmentCheck,
    compare_adjusted_data,
)
from dyadtetrade.modules.market_data.bulk_plan import CapacityEstimate, DatasetSpec
from dyadtetrade.modules.market_data.derivation import DerivedPeriodBuilder
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline
from dyadtetrade.modules.market_data.service import MarketDataService
from dyadtetrade.modules.market_data.stitching import MarketBarStitchingService, StitchResult

__all__ = [
    "AdjustmentCheck",
    "CapacityEstimate",
    "DatasetSpec",
    "DerivedPeriodBuilder",
    "MarketDataPipeline",
    "MarketDataService",
    "MarketBarStitchingService",
    "StitchResult",
    "compare_adjusted_data",
]
