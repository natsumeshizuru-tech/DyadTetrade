from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import backtrader as bt
import pandas as pd

from dyadtetrade.core.config import get_settings

REQUIRED_COLUMNS = {"datetime", "open", "high", "low", "close", "volume"}


class BuyAndHold(bt.Strategy):
    """Minimal offline smoke strategy; it never connects to a trading gateway."""

    params = (("size", 100),)

    def __init__(self) -> None:
        self.submitted = False

    def next(self) -> None:
        if not self.submitted:
            self.buy(size=self.p.size)
            self.submitted = True


def quality_summary(frame: pd.DataFrame) -> dict[str, Any]:
    missing_columns = sorted(REQUIRED_COLUMNS.difference(frame.columns))
    if missing_columns:
        raise ValueError(f"missing required columns: {missing_columns}")
    null_counts = {
        column: int(frame[column].isna().sum())
        for column in sorted(REQUIRED_COLUMNS.difference({"datetime"}))
    }
    ohlc_invalid = (frame["high"] < frame[["open", "close", "low"]].max(axis=1)) | (
        frame["low"] > frame[["open", "close", "high"]].min(axis=1)
    )
    return {
        "rows": int(len(frame)),
        "empty": bool(frame.empty),
        "duplicate_datetimes": int(frame["datetime"].duplicated().sum()),
        "null_counts": null_counts,
        "ohlc_relationship_errors": int(ohlc_invalid.sum()),
        "negative_volume_rows": int((frame["volume"] < 0).sum()),
    }


def run(input_path: Path, output_path: Path) -> dict[str, Any]:
    settings = get_settings()
    if settings.enable_live_trading:
        raise RuntimeError("safe smoke test requires ENABLE_LIVE_TRADING=false")

    frame = pd.read_parquet(input_path).sort_values("datetime").reset_index(drop=True)
    quality = quality_summary(frame)
    if quality["empty"] or quality["ohlc_relationship_errors"]:
        raise ValueError(f"historical data failed quality gate: {quality}")

    feed_frame = frame.copy()
    if "suspended" in feed_frame:
        feed_frame = feed_frame.loc[~feed_frame["suspended"].fillna(False)].copy()
    feed_frame["openinterest"] = 0.0
    feed_frame = feed_frame.set_index(pd.to_datetime(feed_frame["datetime"]))

    engine = bt.Cerebro(stdstats=False)
    engine.broker.setcash(1_000_000.0)
    engine.broker.setcommission(commission=0.0003)
    engine.adddata(bt.feeds.PandasData(dataname=feed_frame))
    engine.addstrategy(BuyAndHold)
    engine.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

    initial_value = float(engine.broker.getvalue())
    results = engine.run()
    final_value = float(engine.broker.getvalue())
    drawdown = results[0].analyzers.drawdown.get_analysis()
    report = {
        "mode": "offline_backtest",
        "live_trading_enabled": False,
        "xttrader_imported": False,
        "input": str(input_path),
        "symbol": str(frame["symbol"].iloc[0]) if "symbol" in frame else None,
        "start": str(frame["datetime"].iloc[0]),
        "end": str(frame["datetime"].iloc[-1]),
        "quality": quality,
        "backtest": {
            "strategy": "buy_and_hold_100_share_smoke",
            "initial_value": initial_value,
            "final_value": final_value,
            "total_return": final_value / initial_value - 1.0,
            "max_drawdown_percent": float(drawdown.get("max", {}).get("drawdown", 0.0)),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the no-live-trading skills smoke loop")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("reports/skills/minimal-loop.json"))
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
