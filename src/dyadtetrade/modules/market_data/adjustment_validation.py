from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class AdjustmentCheck:
    symbol: str
    period: str
    raw_rows: int
    adjusted_rows: int
    common_rows: int
    missing_in_raw: int
    missing_in_adjusted: int
    volume_mismatch_rows: int
    inconsistent_price_ratio_rows: int
    passed: bool


def compare_adjusted_data(
    raw: pd.DataFrame,
    adjusted: pd.DataFrame,
    *,
    symbol: str,
    period: str,
    tolerance: float = 1e-6,
) -> AdjustmentCheck:
    """复核不复权与复权序列，而不假定复权因子必须单调。

    同一时点应具有相同成交量。QMT 复权可能同时包含比例和现金分红平移，
    因此单根 Bar 使用由 high/low 解出的仿射变换复核 open/close，而不是
    错误地假设四个价格必须具有完全相同的乘法比例。
    """

    columns = ["datetime", "open", "high", "low", "close", "volume"]
    for name, frame in (("raw", raw), ("adjusted", adjusted)):
        missing = sorted(set(columns) - set(frame.columns))
        if missing:
            raise ValueError(f"{name} 数据缺少字段：{', '.join(missing)}")
    left = raw[columns].copy()
    right = adjusted[columns].copy()
    left["datetime"] = pd.to_datetime(left["datetime"], errors="raise")
    right["datetime"] = pd.to_datetime(right["datetime"], errors="raise")
    left = left.drop_duplicates("datetime", keep="last").set_index("datetime")
    right = right.drop_duplicates("datetime", keep="last").set_index("datetime")
    common = left.index.intersection(right.index)
    joined = left.loc[common].join(right.loc[common], lsuffix="_raw", rsuffix="_adj")

    volume_diff = (joined["volume_raw"] - joined["volume_adj"]).abs() > tolerance * joined[
        ["volume_raw", "volume_adj"]
    ].abs().max(axis=1).clip(lower=1.0)
    raw_span = joined["high_raw"] - joined["low_raw"]
    adjusted_span = joined["high_adj"] - joined["low_adj"]
    nonflat = raw_span.abs() > tolerance
    scale = adjusted_span.loc[nonflat] / raw_span.loc[nonflat]
    offset = joined.loc[nonflat, "low_adj"] - scale * joined.loc[nonflat, "low_raw"]
    residuals = pd.DataFrame(
        {
            field: (
                joined.loc[nonflat, f"{field}_adj"]
                - (scale * joined.loc[nonflat, f"{field}_raw"] + offset)
            ).abs()
            for field in ("open", "close")
        }
    )
    allowed = tolerance * joined.loc[nonflat, ["open_adj", "close_adj"]].abs().max(axis=1).clip(
        lower=1.0
    )
    inconsistent = residuals.max(axis=1) > allowed
    flat = ~nonflat
    if flat.any():
        adjusted_flat_span = adjusted_span.loc[flat].abs() > tolerance
        inconsistent = pd.concat([inconsistent, adjusted_flat_span])
    missing_in_raw = len(right.index.difference(left.index))
    missing_in_adjusted = len(left.index.difference(right.index))
    volume_mismatches = int(volume_diff.sum())
    ratio_mismatches = int(inconsistent.sum())
    return AdjustmentCheck(
        symbol=symbol,
        period=period,
        raw_rows=len(left),
        adjusted_rows=len(right),
        common_rows=len(common),
        missing_in_raw=missing_in_raw,
        missing_in_adjusted=missing_in_adjusted,
        volume_mismatch_rows=volume_mismatches,
        inconsistent_price_ratio_rows=ratio_mismatches,
        passed=(
            bool(common.size)
            and missing_in_raw == 0
            and missing_in_adjusted == 0
            and volume_mismatches == 0
            and ratio_mismatches == 0
        ),
    )


def write_adjustment_report(checks: list[AdjustmentCheck], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "symbols": len(checks),
            "passed": sum(item.passed for item in checks),
            "failed": sum(not item.passed for item in checks),
        },
        "checks": [asdict(item) for item in checks],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output.resolve()
