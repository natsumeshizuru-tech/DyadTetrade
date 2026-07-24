from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def normalize_trade_date(value: object) -> str | None:
    """Normalize XtQuant calendar values without shifting China dates to UTC."""

    if isinstance(value, datetime):
        observed = value
        if observed.tzinfo is not None:
            observed = observed.astimezone(CHINA_TZ)
        return observed.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) >= 8 and digits[:2] in {"19", "20"}:
        candidate = digits[:8]
        try:
            date.fromisoformat(f"{candidate[:4]}-{candidate[4:6]}-{candidate[6:8]}")
        except ValueError:
            return None
        return candidate
    if not digits:
        return None
    try:
        numeric = int(digits)
        divisor = 1000 if numeric > 10**12 else 1
        return datetime.fromtimestamp(numeric / divisor, tz=CHINA_TZ).strftime("%Y%m%d")
    except (OverflowError, OSError, ValueError):
        return None


def sanitize_trading_days(values: Iterable[object], *, start_date: str, end_date: str) -> list[str]:
    if start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    days: set[str] = set()
    for value in values:
        normalized = normalize_trade_date(value)
        if normalized is None or not start_date <= normalized <= end_date:
            continue
        parsed = date.fromisoformat(f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:8]}")
        if parsed.weekday() < 5:
            days.add(normalized)
    return sorted(days)


def is_trading_day(value: date, trading_days: Iterable[str]) -> bool:
    return value.weekday() < 5 and value.strftime("%Y%m%d") in set(trading_days)
