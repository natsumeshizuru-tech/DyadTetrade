"""Compare local dynamic adjustments with the read-only XtData implementation."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd

from dyadtetrade.data.compat.xtdata_compat import LocalXtData
from dyadtetrade.data.config import load_data_config
from dyadtetrade.data.models import AdjustmentMode
from dyadtetrade.data.xtdata_gateway import build_xtdata_gateway

MODES = ("none", "front", "back", "front_ratio", "back_ratio")
FIELDS = ("open", "high", "low", "close", "volume", "amount")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/market_data.yaml"))
    parser.add_argument("--start", default="20240101")
    parser.add_argument("--end", default="20260717")
    parser.add_argument("--symbols", type=int, default=20)
    parser.add_argument("--tolerance", type=float, default=1e-8)
    parser.add_argument("--neutral-atol", type=float, default=1e-5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    settings = load_data_config(args.config)
    if settings.qmt.site_packages is None:
        raise RuntimeError("qmt.site_packages is required")
    gateway = build_xtdata_gateway(settings.qmt)
    local = LocalXtData(settings)
    symbols = _select_symbols(settings.storage.root / settings.storage.catalog, args.symbols)
    comparisons: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for symbol in symbols:
        neutral_baseline: pd.DataFrame | None = None
        for mode in MODES:
            try:
                local_frame = local.get_market_data_ex(
                    list(FIELDS),
                    [symbol],
                    "1d",
                    args.start,
                    args.end,
                    -1,
                    cast(AdjustmentMode, mode),
                    False,
                )[symbol]
                vendor_frame = gateway.get_adjusted_data(
                    [symbol],
                    fields=list(FIELDS),
                    period="1d",
                    start=args.start,
                    end=args.end,
                    dividend_type=mode,
                )[symbol]
                if neutral_baseline is None:
                    neutral_baseline = local_frame.loc[:, ["volume", "amount"]].copy()
                comparisons.append(
                    _compare(
                        symbol,
                        mode,
                        local_frame,
                        vendor_frame,
                        neutral_baseline,
                        args.tolerance,
                        args.neutral_atol,
                    )
                )
            except Exception as exc:
                failures.append(
                    {"symbol": symbol, "mode": mode, "error": f"{type(exc).__name__}: {exc}"}
                )
    passed = not failures and all(bool(item["passed"]) for item in comparisons)
    payload = {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "start": args.start,
        "end": args.end,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "modes": list(MODES),
        "tolerance": args.tolerance,
        "neutral_atol": args.neutral_atol,
        "comparison_count": len(comparisons),
        "passed": passed,
        "failures": failures,
        "comparisons": comparisons,
    }
    output = args.output or Path(
        f"reports/data/adjustment_golden_{datetime.now(ZoneInfo('Asia/Shanghai')):%Y%m%d}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed else 2


def _select_symbols(catalog_path: Path, count: int) -> list[str]:
    with sqlite3.connect(catalog_path) as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT ca.symbol FROM corporate_actions ca "
                "JOIN instruments i ON i.symbol=ca.symbol "
                "WHERE i.is_active=1 AND i.instrument_type='stock' "
                "GROUP BY ca.symbol ORDER BY count(*) DESC, ca.symbol LIMIT ?",
                (count,),
            )
        ]


def _compare(
    symbol: str,
    mode: str,
    local: pd.DataFrame,
    vendor: pd.DataFrame,
    local_neutral_baseline: pd.DataFrame,
    tolerance: float,
    neutral_atol: float,
) -> dict[str, Any]:
    local_aligned = local.copy()
    vendor_aligned = vendor.copy()
    local_aligned.index = pd.DatetimeIndex(local_aligned.index).strftime("%Y%m%d")
    vendor_aligned.index = [str(value)[:8] for value in vendor_aligned.index]
    common = local_aligned.index.intersection(vendor_aligned.index)
    price_columns = ["open", "high", "low", "close"]
    neutral_columns = ["volume", "amount"]
    price_difference = (
        local_aligned.loc[common, price_columns].astype(float)
        - vendor_aligned.loc[common, price_columns].astype(float)
    ).abs()
    local_neutral = local_aligned.loc[common, neutral_columns].astype(float)
    vendor_neutral = vendor_aligned.loc[common, neutral_columns].astype(float)
    neutral_difference = (local_neutral - vendor_neutral).abs()
    neutral_scale = local_neutral.abs().where(
        local_neutral.abs() >= vendor_neutral.abs(), vendor_neutral.abs()
    )
    neutral_scale = neutral_scale.clip(lower=1.0)
    neutral_relative_difference = neutral_difference / neutral_scale
    neutral_match_cells = (local_neutral.isna() & vendor_neutral.isna()) | (
        local_neutral.notna()
        & vendor_neutral.notna()
        & ((neutral_difference <= neutral_atol) | (neutral_relative_difference <= tolerance))
    )
    neutral_match = bool(neutral_match_cells.to_numpy().all())
    baseline = local_neutral_baseline.copy()
    baseline.index = pd.DatetimeIndex(baseline.index).strftime("%Y%m%d")
    local_neutral_difference = (
        local_aligned.loc[common, neutral_columns].astype(float)
        - baseline.loc[common, neutral_columns].astype(float)
    ).abs()
    max_price = 0.0 if price_difference.empty else float(price_difference.max().max())
    max_neutral = 0.0 if neutral_difference.empty else float(neutral_difference.max().max())
    max_relative_neutral = (
        0.0 if neutral_relative_difference.empty else float(neutral_relative_difference.max().max())
    )
    max_local_neutral = (
        0.0 if local_neutral_difference.empty else float(local_neutral_difference.max().max())
    )
    rows_match = len(common) == len(local_aligned) == len(vendor_aligned)
    return {
        "symbol": symbol,
        "mode": mode,
        "rows": len(common),
        "rows_match": rows_match,
        "max_abs_price_difference": max_price,
        "max_abs_volume_amount_difference": max_neutral,
        "max_relative_volume_amount_difference": max_relative_neutral,
        "max_local_volume_amount_mode_difference": max_local_neutral,
        "volume_amount_match": neutral_match,
        "passed": (
            rows_match and max_price <= tolerance and neutral_match and max_local_neutral == 0.0
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
