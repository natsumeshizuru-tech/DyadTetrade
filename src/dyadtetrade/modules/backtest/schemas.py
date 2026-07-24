from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dyadtetrade.domain import Interval


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_name: str
    strategy_parameters: dict[str, object] = Field(default_factory=dict)
    symbols: list[str]
    interval: Interval = Interval.DAY_1
    start_date: date | None = None
    end_date: date | None = None
    initial_cash: Decimal = Field(default=Decimal("1000000"), gt=0)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0)
    minimum_commission: Decimal = Field(default=Decimal("5"), ge=0)
    stamp_duty_rate: Decimal = Field(default=Decimal("0.0005"), ge=0)
    transfer_fee_rate: Decimal = Field(default=Decimal("0"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("0"), ge=0)
    max_volume_participation: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    allow_partial_fills: bool = True
    enforce_t1: bool = True
    conservative_limit_board: bool = True
    execution_delay_bars: int = Field(default=1, ge=1, le=20)
    random_seed: int = 0
    benchmark_symbol: str | None = "000300.SH"
    data_version: str = "bt_ready-v1"

    @model_validator(mode="after")
    def validate_dates_and_symbols(self) -> BacktestConfig:
        if not self.symbols:
            raise ValueError("backtest requires at least one symbol")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("backtest symbols must be unique")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        return self


class BacktestTaskCreate(BaseModel):
    config: BacktestConfig
    stock_pool_id: int | None = None
    idempotency_key: str | None = None


class BacktestTaskView(BaseModel):
    id: int
    status: str
    progress: float
    strategy_name: str
    symbols: list[str]
    result_id: int | None = None
    error_message: str | None = None
