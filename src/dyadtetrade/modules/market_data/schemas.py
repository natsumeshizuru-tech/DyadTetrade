from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from dyadtetrade.core.symbols import normalize_symbol


class HistoryDownloadRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=20)
    start_date: str = Field(pattern=r"^\d{8}$")
    end_date: str = Field(default="", pattern=r"^$|^\d{8}$")
    period: str = Field(default="1d", pattern=r"^(tick|1m|5m|15m|30m|1h|1d|1w|1mon|1q|1y)$")
    adjust: Literal["none", "front", "back", "front_ratio", "back_ratio"] = "front"
    incrementally: bool = True

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_symbol(value) for value in values))


class StoredFile(BaseModel):
    symbol: str
    period: str
    adjust: str
    layer: str
    file_path: str
    start_date: str | None
    end_date: str | None
    rows: int
    file_size: int


class DownloadResult(BaseModel):
    files: list[StoredFile]
    errors: dict[str, str]


class DownloadTaskCreate(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=10000)
    start_date: str = Field(pattern=r"^\d{8}$")
    end_date: str = Field(default="", pattern=r"^$|^\d{8}$")
    period: str = Field(default="1d", pattern=r"^(tick|1m|5m|15m|30m|1h|1d|1w|1mon|1q|1y)$")
    adjust: Literal["none", "front", "back", "front_ratio", "back_ratio"] = "front"
    max_retries: int = Field(default=2, ge=0, le=10)
    execute_now: bool = False

    @field_validator("symbols")
    @classmethod
    def normalize_task_symbols(cls, values: list[str]) -> list[str]:
        return sorted({normalize_symbol(value) for value in values})


class DownloadTaskResult(BaseModel):
    id: int
    idempotency_key: str | None
    symbols: list[str]
    period: str
    adjust: str
    start_date: str
    end_date: str
    status: str
    total_items: int
    completed_items: int
    succeeded_items: int
    failed_items: int
    progress: float
    quality_report_json: str | None
    quality_report_csv: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    items: list[dict[str, object]] | None = None


class DataCenterSummary(BaseModel):
    instrument_count: int
    stock_pool_count: int
    task_count: int
    running_task_count: int
    quality_issue_count: int
    covered_instrument_count: int
    coverage_percent: float
    latest_trade_date: str | None = None
    latest_trade_date_covered_count: int = 0
    missing_instrument_count: int = 0
    empty_or_error_count: int = 0
    failed_download_item_count: int = 0
    retryable_failed_download_item_count: int = 0
    quarantined_download_item_count: int = 0
    failure_category_counts: dict[str, int] = Field(default_factory=dict)
    last_updated_at: str | None = None


class LegacyImportRequest(BaseModel):
    source_path: str = Field(min_length=1)
    period: str = Field(default="1d", pattern=r"^(1m|5m|15m|30m|1h|1d|1w|1mon|1q|1y)$")
    adjust: Literal["none", "front", "back", "front_ratio", "back_ratio"] = "front"


class UniverseResult(BaseModel):
    count: int
    symbols: list[str]


class CalendarResult(BaseModel):
    market: str
    count: int
    days: list[str]
