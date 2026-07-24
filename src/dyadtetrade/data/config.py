from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

RawPeriod = Literal["1d", "1m", "tick"]


def _default_periods() -> list[RawPeriod]:
    return ["1d", "1m"]


class QmtConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userdata_mini: Path | None = None
    data_dir: Path | None = None
    big_data_dir: Path | None = None
    require_client: bool = True
    site_packages: Path | None = None
    sidecar_python: Path | None = None
    call_timeout_seconds: int = Field(default=300, ge=1)
    retries: int = Field(default=3, ge=0, le=20)
    minimum_cache_free_gib: float = Field(default=10.0, ge=1.0)


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path = Path("data/sqlite")
    catalog: str = "catalog.db"
    daily_bars: str = "bars_1d.db"
    wal: bool = True
    busy_timeout_ms: int = Field(default=60_000, ge=1_000)
    batch_rows: int = Field(default=20_000, ge=100)
    minute_shard: Literal["market_month"] = "market_month"
    minimum_free_gib: float = Field(default=20.0, ge=1.0)


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sectors: list[str] = Field(default_factory=lambda: ["沪深京A股"])
    include_delisted: bool = True
    extra_sectors: list[str] = Field(default_factory=list)


class BackfillConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: str = "20240101"
    requested_end_date: str = "20260718"
    periods: list[RawPeriod] = Field(default_factory=_default_periods)
    enable_tick: bool = False
    download_batch_symbols: int = Field(default=50, ge=1)
    read_batch_symbols: int = Field(default=20, ge=1)
    overlap_trading_days: int = Field(default=3, ge=0, le=30)

    @field_validator("start_date", "requested_end_date")
    @classmethod
    def validate_compact_date(cls, value: str) -> str:
        if len(value) != 8 or not value.isdigit():
            raise ValueError("dates must use YYYYMMDD")
        return value


def _default_financial_tables() -> list[str]:
    return [
        "Balance",
        "Income",
        "CashFlow",
        "Capital",
        "Top10FlowHolder",
        "Top10Holder",
        "HolderNum",
        "PershareIndex",
    ]


class FinancialConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tables: list[str] = Field(default_factory=_default_financial_tables)
    download_batch_symbols: int = Field(default=20, ge=1, le=500)
    source: str = "xtdata_financial_v1"


class ReferenceDataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_all_sectors: bool = True
    index_sectors: list[str] = Field(default_factory=lambda: ["沪深指数"])
    extra_index_codes: list[str] = Field(default_factory=lambda: ["899050.BJ"])
    write_batch_items: int = Field(default=250, ge=1, le=2_000)


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    run_time: str = "16:10"
    retries: int = Field(default=6, ge=0)
    retry_delay_seconds: int = Field(default=300, ge=1)
    minimum_coverage_ratio: float = Field(default=0.98, ge=0, le=1)


class ArchiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path = Path("data/archive/market_data")
    catalog_daily_backup: bool = True
    seal_previous_month: bool = True
    checksum: Literal["sha256"] = "sha256"
    retention_daily_catalog_days: int = Field(default=30, ge=1)


class HistoricalBacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_adjustment: Literal["none", "front", "back", "front_ratio", "back_ratio"] = "none"
    adjustment_anchor: Literal["query_end"] = "query_end"
    forbid_live_trading_imports: bool = True
    result_root: Path = Path("reports/backtests/sqlite")


class DataPlatformSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qmt: QmtConfig = Field(default_factory=QmtConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    backfill: BackfillConfig = Field(default_factory=BackfillConfig)
    financial: FinancialConfig = Field(default_factory=FinancialConfig)
    reference_data: ReferenceDataConfig = Field(default_factory=ReferenceDataConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)
    backtest: HistoricalBacktestConfig = Field(default_factory=HistoricalBacktestConfig)


def _parse_environment_value(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _environment_overrides(prefix: str = "DYADTETRADE_DATA__") -> dict[str, Any]:
    root: dict[str, Any] = {}
    for name, raw_value in os.environ.items():
        if not name.upper().startswith(prefix):
            continue
        path = [item.lower() for item in name[len(prefix) :].split("__") if item]
        if not path:
            continue
        cursor = root
        for part in path[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"environment override collision at {name}")
            cursor = child
        cursor[path[-1]] = _parse_environment_value(raw_value)
    return root


def _deep_merge(target: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = dict(target)
    for key, value in overrides.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def _load_yaml_mapping(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is not None and not isinstance(raw, dict):
        raise ValueError(f"market-data config must contain a mapping: {config_path}")
    return dict(raw or {})


def load_data_config(path: Path | str = Path("config/market_data.yaml")) -> DataPlatformSettings:
    config_path = Path(path)
    payload = _load_yaml_mapping(config_path)
    local_path = config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")
    payload = _deep_merge(payload, _load_yaml_mapping(local_path))
    return DataPlatformSettings.model_validate(_deep_merge(payload, _environment_overrides()))
