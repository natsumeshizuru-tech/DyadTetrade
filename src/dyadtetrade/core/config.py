from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ExecutionMode(StrEnum):
    DISABLED = "disabled"
    PAPER = "paper"
    QMT_SIM = "qmt_sim"
    QMT_LIVE = "qmt_live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DYADTETRADE_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "DyadTetrade"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    trading_engine_host: str = "127.0.0.1"
    trading_engine_port: int = 8011
    trading_engine_url: str = "http://127.0.0.1:8011/internal/v1"
    trading_engine_token: str = Field(default="", repr=False)
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    sqlite_url: str = "sqlite+aiosqlite:///./data/sqlite/dyadtetrade.db"
    duckdb_path: Path = Path("data/duckdb/research.duckdb")
    data_root: Path = Path("data")

    enable_live_trading: bool = False
    execution_mode: ExecutionMode = ExecutionMode.DISABLED
    enable_qmt_sim_trading: bool = False
    enable_qmt_live_trading: bool = False
    qmt_account_id: str = Field(default="", repr=False)
    qmt_account_type: str = "STOCK"
    qmt_userdata_path: Path | None = Field(default=None, repr=False)
    qmt_account_is_simulated: bool = False
    max_order_notional: Decimal = Decimal("10000")
    max_daily_notional: Decimal = Decimal("50000")
    max_orders_per_minute: int = 5
    max_symbol_position_notional: Decimal = Decimal("20000")
    max_total_position_notional: Decimal = Decimal("100000")
    max_total_position_ratio: Decimal = Decimal("0.80")
    max_daily_orders: int = 100
    max_strategy_daily_notional: Decimal = Decimal("20000")
    allowed_symbols: Annotated[list[str], NoDecode] = Field(default_factory=list)
    enable_dynamic_execution_universe: bool = False
    execution_universe_release_id: str = ""
    enable_full_market_live_trading: bool = False
    full_market_live_confirmation: str = Field(default="", repr=False)
    full_market_live_release_date: str = ""
    enable_automated_strategy_execution: bool = False
    automated_live_confirmation: str = Field(default="", repr=False)
    automated_live_release_date: str = ""
    qmt_live_account_whitelist: Annotated[list[str], NoDecode] = Field(
        default_factory=list, repr=False
    )
    qmt_live_confirmation: str = Field(default="", repr=False)
    qmt_live_release_date: str = ""
    user_strategies_path: Path = Path("user_strategies")
    strategy_bundle_inbox: Path = Path("data/strategy_bundles/inbox")
    allowed_strategy_bundle_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "buy_and_hold",
            "moving_average_cross",
            "low_volume_ratio_momentum",
            "example_momentum",
        ]
    )
    strategy_runtime_api_version: str = "1.0"
    strategy_data_capabilities: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "bars.tick.none",
            "bars.1m.none",
            "bars.5m.none",
            "bars.15m.none",
            "bars.30m.none",
            "bars.1h.none",
            "bars.1d.none",
        ]
    )
    enable_realtime_compute: bool = True
    realtime_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["600519.SH", "510300.SH"]
    )
    realtime_poll_interval_seconds: float = Field(default=2.0, ge=0.5, le=60)
    realtime_factor_lookback: int = Field(default=20, ge=2, le=1000)
    realtime_small_cap_candidates: int = Field(default=50, ge=0, le=500)
    xtquant_path: Path | None = None
    market_download_max_retries: int = 2
    market_download_retry_backoff_seconds: float = 0.25
    market_pipeline_version: str = "market-v1"

    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]

    @field_validator(
        "allowed_symbols",
        "realtime_symbols",
        "qmt_live_account_whitelist",
        "allowed_strategy_bundle_ids",
        "strategy_data_capabilities",
        mode="before",
    )
    @classmethod
    def parse_allowed_symbols(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        return value

    def validate_execution_safety(self) -> None:
        positive_limits = (
            self.max_order_notional,
            self.max_daily_notional,
            self.max_symbol_position_notional,
            self.max_total_position_notional,
            self.max_strategy_daily_notional,
        )
        if any(value <= 0 for value in positive_limits):
            raise RuntimeError("execution notional limits must be positive")
        if not Decimal("0") < self.max_total_position_ratio <= Decimal("1"):
            raise RuntimeError("MAX_TOTAL_POSITION_RATIO must be in (0, 1]")
        if self.max_orders_per_minute <= 0 or self.max_daily_orders <= 0:
            raise RuntimeError("execution order count limits must be positive")
        if self.execution_mode is ExecutionMode.QMT_LIVE:
            missing: list[str] = []
            if not self.enable_live_trading:
                missing.append("ENABLE_LIVE_TRADING")
            if not self.enable_qmt_live_trading:
                missing.append("ENABLE_QMT_LIVE_TRADING")
            if not self.qmt_account_id.strip():
                missing.append("QMT_ACCOUNT_ID")
            if self.qmt_account_id not in self.qmt_live_account_whitelist:
                missing.append("QMT_LIVE_ACCOUNT_WHITELIST(account match)")
            if self.qmt_live_confirmation != "I_CONFIRM_QMT_LIVE":
                missing.append("QMT_LIVE_CONFIRMATION")
            if self.qmt_live_release_date != date.today().isoformat():
                missing.append("QMT_LIVE_RELEASE_DATE(today)")
            if self.qmt_userdata_path is None or not self.qmt_userdata_path.exists():
                missing.append("QMT_USERDATA_PATH(existing directory)")
            if not self.allowed_symbols and not self.enable_dynamic_execution_universe:
                missing.append("ALLOWED_SYMBOLS or DYNAMIC_EXECUTION_UNIVERSE")
            if self.enable_dynamic_execution_universe:
                if not self.execution_universe_release_id:
                    missing.append("EXECUTION_UNIVERSE_RELEASE_ID")
                if not self.enable_full_market_live_trading:
                    missing.append("ENABLE_FULL_MARKET_LIVE_TRADING")
                if self.full_market_live_confirmation != "I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE":
                    missing.append("FULL_MARKET_LIVE_CONFIRMATION")
                if self.full_market_live_release_date != date.today().isoformat():
                    missing.append("FULL_MARKET_LIVE_RELEASE_DATE(today)")
            if self.enable_automated_strategy_execution:
                if self.automated_live_confirmation != "I_CONFIRM_AUTOMATED_LIVE_EXECUTION":
                    missing.append("AUTOMATED_LIVE_CONFIRMATION")
                if self.automated_live_release_date != date.today().isoformat():
                    missing.append("AUTOMATED_LIVE_RELEASE_DATE(today)")
            if missing:
                raise RuntimeError("qmt_live release gate is incomplete: " + ", ".join(missing))
            return
        if self.enable_live_trading:
            raise RuntimeError("ENABLE_LIVE_TRADING requires execution_mode=qmt_live")
        if self.execution_mode is not ExecutionMode.QMT_SIM:
            return
        sim_missing: list[str] = []
        if not self.enable_qmt_sim_trading:
            sim_missing.append("ENABLE_QMT_SIM_TRADING")
        if not self.qmt_account_id.strip():
            sim_missing.append("QMT_ACCOUNT_ID")
        if self.qmt_userdata_path is None:
            sim_missing.append("QMT_USERDATA_PATH")
        elif not self.qmt_userdata_path.exists():
            sim_missing.append("QMT_USERDATA_PATH(existing directory)")
        if not self.allowed_symbols and not self.enable_dynamic_execution_universe:
            sim_missing.append("ALLOWED_SYMBOLS or DYNAMIC_EXECUTION_UNIVERSE")
        if self.enable_dynamic_execution_universe and not self.execution_universe_release_id:
            sim_missing.append("EXECUTION_UNIVERSE_RELEASE_ID")
        if sim_missing:
            raise RuntimeError("qmt_sim configuration is incomplete: " + ", ".join(sim_missing))

    def ensure_directories(self) -> None:
        for path in (
            self.data_root / "raw",
            self.data_root / "clean",
            self.data_root / "trusted",
            self.data_root / "bt_ready",
            self.duckdb_path.parent,
            Path("data/sqlite"),
            Path("logs"),
            Path("reports"),
            self.user_strategies_path,
            self.strategy_bundle_inbox,
            self.strategy_bundle_inbox.parent / "receipts",
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
