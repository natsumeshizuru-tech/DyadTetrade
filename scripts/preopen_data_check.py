"""Fail-closed pre-open validation for the latest trusted daily partition."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dyadtetrade.integrations.xtquant import XtQuantGateway

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def expected_completed_trade_day(values: list[str], now: datetime | None = None) -> str:
    observed = (now or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ)
    cutoff = observed.date()
    if observed.time() < time(18):
        cutoff -= timedelta(days=1)
    candidates: list[date] = []
    for value in values:
        parsed = date.fromisoformat(
            value.replace("/", "-")
            if "-" in value or "/" in value
            else f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        )
        if parsed.weekday() < 5 and parsed <= cutoff:
            candidates.append(parsed)
    if not candidates:
        raise RuntimeError("miniQMT returned no completed recent trading day")
    return max(candidates).strftime("%Y%m%d")


def main() -> None:
    end = date.today()
    start = end - timedelta(days=14)
    trading_days = XtQuantGateway().trading_days(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    if not trading_days:
        raise RuntimeError("miniQMT returned no recent trading days")
    expected = expected_completed_trade_day(trading_days)

    database = Path("data/sqlite/dyadtetrade.db")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "select max(end_date) from data_files where layer='trusted' and period='1d'"
        ).fetchone()
    actual = None if row is None else row[0]
    # XtData's local calendar can lag its completed daily partitions by one day
    # immediately after a session. A partition newer than the calendar is safe;
    # only an older partition is a pre-open failure.
    if actual is None or actual < expected:
        raise RuntimeError(f"trusted daily data is stale: actual={actual}, expected={expected}")
    print({"latest": actual, "expected": expected})


if __name__ == "__main__":
    main()
