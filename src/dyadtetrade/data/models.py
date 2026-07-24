from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

AdjustmentMode = Literal["none", "front", "back", "front_ratio", "back_ratio"]
Period = Literal["1d", "1m", "tick"]


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    symbol: str
    code: str
    market: str
    name: str | None
    instrument_type: str | None
    list_date: int | None
    delist_date: int | None
    is_active: bool
    price_tick: float | None
    volume_multiple: float | None
    raw_json: str | None
    source: str = "xtdata"


@dataclass(frozen=True, slots=True)
class CorporateActionRecord:
    symbol: str
    action_date: int
    interest: float = 0.0
    stock_bonus: float = 0.0
    stock_gift: float = 0.0
    allot_num: float = 0.0
    allot_price: float = 0.0
    gugai: int = 0
    dr: float = 1.0
    raw_json: str | None = None
    source: str = "xtdata"


@dataclass(frozen=True, slots=True)
class BarRecord:
    symbol: str
    ts: int
    trade_date: int
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    amount: float | None
    settlement_price: float | None = None
    open_interest: float | None = None
    pre_close: float | None = None
    suspend_flag: int | None = None
    source: str = "xtdata"
    ingested_at: int = 0
    source_hash: str | None = None

    def sqlite_values(self) -> tuple[object, ...]:
        return (
            self.symbol,
            self.ts,
            self.trade_date,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.amount,
            self.settlement_price,
            self.open_interest,
            self.pre_close,
            self.suspend_flag,
            self.source,
            self.ingested_at,
            self.source_hash,
        )


@dataclass(frozen=True, slots=True)
class FinancialRecord:
    symbol: str
    table_name: str
    report_date: int
    announcement_date: int
    row_ordinal: int
    raw_json: str
    source: str
    ingested_at: int
    source_hash: str

    def sqlite_values(self) -> tuple[object, ...]:
        return (
            self.symbol,
            self.table_name,
            self.report_date,
            self.announcement_date,
            self.row_ordinal,
            self.raw_json,
            self.source,
            self.ingested_at,
            self.source_hash,
        )


@dataclass(frozen=True, slots=True)
class QualityIssueRecord:
    severity: Literal["warning", "error"]
    symbol: str | None
    period: str | None
    trade_date: int | None
    rule: str
    details: dict[str, Any]
    job_id: str | None = None
    shard: str | None = None
