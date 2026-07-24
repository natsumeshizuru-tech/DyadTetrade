from __future__ import annotations

from enum import StrEnum


class Exchange(StrEnum):
    SH = "SH"
    SZ = "SZ"
    BJ = "BJ"
    CFFEX = "CFFEX"
    UNKNOWN = "UNKNOWN"


class InstrumentType(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    FUND = "fund"
    BOND = "bond"
    CONVERTIBLE_BOND = "convertible_bond"
    FUTURE = "future"
    UNKNOWN = "unknown"


class Direction(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class PriceType(StrEnum):
    NEXT_BAR_OPEN = "next_bar_open"
    LIMIT = "limit"
    LATEST = "latest"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    RISK_REJECTED = "RISK_REJECTED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class TradingStatus(StrEnum):
    TRADING = "trading"
    SUSPENDED = "suspended"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class RiskOperatingState(StrEnum):
    NORMAL = "NORMAL"
    REDUCE_ONLY = "REDUCE_ONLY"
    CANCEL_ONLY = "CANCEL_ONLY"
    HALTED = "HALTED"
    RECOVERY = "RECOVERY"


class EventType(StrEnum):
    TIMER = "timer"
    BAR = "bar"
    TICK = "tick"
    QUOTE = "quote"
    SIGNAL = "signal"
    ORDER = "order"
    TRADE = "trade"
    POSITION = "position"
    ACCOUNT = "account"
    RISK = "risk"
    STRATEGY = "strategy"
    GATEWAY = "gateway"
    ERROR = "error"


class Interval(StrEnum):
    TICK = "tick"
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_30 = "30m"
    HOUR_1 = "1h"
    DAY_1 = "1d"
    WEEK_1 = "1w"
