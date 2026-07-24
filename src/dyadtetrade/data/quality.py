from __future__ import annotations

import re
from collections import Counter

from dyadtetrade.data.models import BarRecord, QualityIssueRecord

SYMBOL_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")


def validate_bars(
    bars: list[BarRecord],
    *,
    period: str,
    requested_end: int,
    open_days: set[int],
    job_id: str | None = None,
) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []
    counts = Counter((bar.symbol, bar.ts) for bar in bars)
    for (symbol, ts), count in counts.items():
        if count > 1:
            issues.append(
                QualityIssueRecord(
                    "error",
                    symbol,
                    period,
                    None,
                    "duplicate_primary_key",
                    {"ts": ts, "count": count},
                    job_id,
                )
            )
    by_symbol: dict[str, list[BarRecord]] = {}
    for bar in bars:
        by_symbol.setdefault(bar.symbol, []).append(bar)
        issues.extend(_validate_one(bar, period, requested_end, open_days, job_id))
    for symbol, symbol_bars in by_symbol.items():
        timestamps = [bar.ts for bar in symbol_bars]
        if timestamps != sorted(timestamps):
            issues.append(
                QualityIssueRecord(
                    "error",
                    symbol,
                    period,
                    None,
                    "non_monotonic_time",
                    {"row_count": len(timestamps)},
                    job_id,
                )
            )
        for previous, current in zip(symbol_bars, symbol_bars[1:], strict=False):
            if previous.close is not None and current.pre_close is not None and previous.close > 0:
                relative_gap = abs(current.pre_close - previous.close) / previous.close
                if relative_gap > 0.03:
                    issues.append(
                        QualityIssueRecord(
                            "warning",
                            symbol,
                            period,
                            current.trade_date,
                            "pre_close_discontinuity",
                            {
                                "previous_close": previous.close,
                                "pre_close": current.pre_close,
                                "relative_gap": relative_gap,
                            },
                            job_id,
                        )
                    )
    return issues


def hard_failures(issues: list[QualityIssueRecord]) -> list[QualityIssueRecord]:
    return [issue for issue in issues if issue.severity == "error"]


def _validate_one(
    bar: BarRecord,
    period: str,
    requested_end: int,
    open_days: set[int],
    job_id: str | None,
) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []

    def issue(rule: str, details: dict[str, object]) -> None:
        issues.append(
            QualityIssueRecord("error", bar.symbol, period, bar.trade_date, rule, details, job_id)
        )

    if not SYMBOL_PATTERN.fullmatch(bar.symbol):
        issue("invalid_symbol", {"symbol": bar.symbol})
    if bar.trade_date > requested_end:
        issue("after_requested_end", {"requested_end": requested_end})
    if bar.trade_date not in open_days:
        issue("not_open_trading_day", {"trade_date": bar.trade_date})
    prices = [bar.open, bar.high, bar.low, bar.close]
    if any(value is None for value in prices):
        issue("missing_ohlc", {})
    elif any(float(value) < 0 for value in prices if value is not None):
        issue("negative_ohlc", {})
    else:
        assert bar.open is not None
        assert bar.high is not None
        assert bar.low is not None
        assert bar.close is not None
        if bar.high < max(bar.open, bar.close, bar.low):
            issue("invalid_high", {"high": bar.high})
        if bar.low > min(bar.open, bar.close, bar.high):
            issue("invalid_low", {"low": bar.low})
    if bar.volume is not None and bar.volume < 0:
        issue("negative_volume", {"volume": bar.volume})
    if bar.amount is not None and bar.amount < 0:
        issue("negative_amount", {"amount": bar.amount})
    if bar.suspend_flag not in {None, 0, 1}:
        issue("invalid_suspend_flag", {"suspend_flag": bar.suspend_flag})
    return issues
