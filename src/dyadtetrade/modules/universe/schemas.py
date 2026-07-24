from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from dyadtetrade.core.symbols import normalize_symbol


class PoolFilters(BaseModel):
    markets: list[Literal["SH", "SZ", "BJ"]] = []
    asset_types: list[Literal["stock", "etf", "index", "other"]] = []
    industries: list[str] = []
    sectors: list[str] = []
    active_only: bool = True
    symbol_prefixes: list[str] = []


class StockPoolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    pool_type: Literal["custom", "filter", "sector", "industry", "index"] = "custom"
    description: str | None = Field(default=None, max_length=512)
    symbols: list[str] = []
    filters: PoolFilters = PoolFilters()
    is_dynamic: bool = False

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return sorted({normalize_symbol(value) for value in values})


class StockPoolResult(BaseModel):
    id: int
    name: str
    pool_type: str
    description: str | None
    is_dynamic: bool
    member_count: int
    filters: PoolFilters


class CatalogSyncRequest(BaseModel):
    refresh_remote_sectors: bool = False
    include_details: bool = True
    include_all_sectors: bool = True


class CatalogSyncResult(BaseModel):
    instrument_count: int
    stock_count: int
    etf_count: int
    index_count: int
    sector_count: int
