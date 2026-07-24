from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from dyadtetrade.db.session import Base


class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128))
    exchange: Mapped[str] = mapped_column(String(8), index=True)
    asset_type: Mapped[str] = mapped_column(String(32), default="stock")
    industry: Mapped[str | None] = mapped_column(String(128), index=True)
    list_date: Mapped[str | None] = mapped_column(String(8))
    delist_date: Mapped[str | None] = mapped_column(String(8))
    lot_size: Mapped[int | None] = mapped_column(Integer)
    price_tick: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    price_decimals: Mapped[int] = mapped_column(Integer, default=2)
    quantity_decimals: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    money_decimals: Mapped[int] = mapped_column(Integer, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="xtquant")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class TradingDay(Base):
    __tablename__ = "trading_days"

    market: Mapped[str] = mapped_column(String(8), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(8), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="xtquant")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DataFile(Base):
    __tablename__ = "data_files"
    __table_args__ = (UniqueConstraint("file_path", name="uq_data_files_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    period: Mapped[str] = mapped_column(String(16), index=True)
    adjust: Mapped[str] = mapped_column(String(16))
    layer: Mapped[str] = mapped_column(String(16), index=True)
    file_path: Mapped[str] = mapped_column(String(512))
    start_date: Mapped[str | None] = mapped_column(String(8))
    end_date: Mapped[str | None] = mapped_column(String(8))
    rows: Mapped[int] = mapped_column(Integer, default=0)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(16), default="1")
    pipeline_version: Mapped[str] = mapped_column(String(32), default="market-v1")
    imported_from: Mapped[str | None] = mapped_column(String(1024))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class DownloadTask(Base):
    __tablename__ = "download_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True, default="*")
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    symbols_json: Mapped[str] = mapped_column(Text, default="[]")
    period: Mapped[str] = mapped_column(String(16))
    adjust: Mapped[str] = mapped_column(String(16), default="front")
    start_date: Mapped[str] = mapped_column(String(8))
    end_date: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    quality_report_json: Mapped[str | None] = mapped_column(String(1024))
    quality_report_csv: Mapped[str | None] = mapped_column(String(1024))
    error_message: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class DownloadTaskItem(Base):
    __tablename__ = "download_task_items"
    __table_args__ = (UniqueConstraint("task_id", "symbol", name="uq_download_task_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("download_tasks.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    start_date: Mapped[str | None] = mapped_column(String(8))
    end_date: Mapped[str | None] = mapped_column(String(8))
    error_message: Mapped[str | None] = mapped_column(String(1024))
    failure_category: Mapped[str | None] = mapped_column(String(32), index=True)
    failure_details_json: Mapped[str] = mapped_column(Text, default="{}")
    retryable: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(32), default="sector", index=True)
    source: Mapped[str] = mapped_column(String(32), default="xtquant")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class SectorMember(Base):
    __tablename__ = "sector_members"
    __table_args__ = (UniqueConstraint("sector_id", "symbol", name="uq_sector_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"), index=True)
    weight: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="xtquant")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class SectorMemberSnapshot(Base):
    __tablename__ = "sector_member_snapshots"
    __table_args__ = (
        UniqueConstraint("sector_id", "symbol", "as_of_date", name="uq_sector_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sectors.id"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"), index=True)
    as_of_date: Mapped[str] = mapped_column(String(8), index=True)
    weight: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="xtquant")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class StockPool(Base):
    __tablename__ = "stock_pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    pool_type: Mapped[str] = mapped_column(String(32), default="custom", index=True)
    description: Mapped[str | None] = mapped_column(String(512))
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    is_dynamic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class StockPoolMember(Base):
    __tablename__ = "stock_pool_members"
    __table_args__ = (UniqueConstraint("pool_id", "symbol", name="uq_pool_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("stock_pools.id"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class StockPoolMemberSnapshot(Base):
    __tablename__ = "stock_pool_member_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "pool_id", "symbol", "effective_from", name="uq_stock_pool_member_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("stock_pools.id"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"), index=True)
    effective_from: Mapped[str] = mapped_column(String(8), index=True)
    effective_to: Mapped[str | None] = mapped_column(String(8), index=True)
    source: Mapped[str] = mapped_column(String(32), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class QualityIssue(Base):
    __tablename__ = "quality_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("download_tasks.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    period: Mapped[str] = mapped_column(String(16), default="1d")
    layer: Mapped[str] = mapped_column(String(16), default="clean")
    check_code: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[str | None] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(String(1024))
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class StrategyRecord(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(1024), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    current_version: Mapped[str] = mapped_column(String(64), default="1.0.0")
    parameters_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    source: Mapped[str] = mapped_column(String(32), default="builtin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class StrategyVersionRecord(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    version: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class StrategyRunRecord(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_instance_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    strategy_name: Mapped[str] = mapped_column(String(128), index=True)
    strategy_version: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="paper", index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    random_seed: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(String(2048))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class StrategyFileRecord(Base):
    __tablename__ = "strategy_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    relative_path: Mapped[str] = mapped_column(String(256), unique=True)
    draft_sha256: Mapped[str] = mapped_column(String(64), index=True)
    validation_status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    smoke_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    published_version: Mapped[int | None] = mapped_column(Integer, index=True)
    published_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_status: Mapped[str] = mapped_column(String(24), default="stopped", index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_error: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class BacktestTaskRecord(Base):
    __tablename__ = "backtest_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    strategy_name: Mapped[str] = mapped_column(String(128), index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(64))
    config_json: Mapped[str] = mapped_column(Text)
    symbols_json: Mapped[str] = mapped_column(Text)
    data_version: Mapped[str] = mapped_column(String(64))
    data_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    result_id: Mapped[int | None] = mapped_column(Integer, index=True)
    error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class BacktestResultRecord(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("backtest_tasks.id"), unique=True, index=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    report_json: Mapped[str | None] = mapped_column(String(1024))
    report_html: Mapped[str | None] = mapped_column(String(1024))
    report_pdf: Mapped[str | None] = mapped_column(String(1024))
    report_svg: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class BacktestOrderRecord(Base):
    __tablename__ = "backtest_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("backtest_results.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    filled_quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    average_fill_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)


class BacktestTradeRecord(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("backtest_results.id"), index=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    commission: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    transfer_fee: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)


class BacktestDailyEquityRecord(Base):
    __tablename__ = "backtest_daily_equity"
    __table_args__ = (UniqueConstraint("result_id", "trade_date", name="uq_result_trade_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("backtest_results.id"), index=True)
    trade_date: Mapped[str] = mapped_column(String(32), index=True)
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    positions_value: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    total_assets: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    return_value: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    net_value: Mapped[float] = mapped_column(Float, default=1.0)
    benchmark_net_value: Mapped[float | None] = mapped_column(Float)
    relative_net_value: Mapped[float | None] = mapped_column(Float)


class FactorDefinitionRecord(Base):
    __tablename__ = "factor_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_factor_definition_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(1024), default="")
    input_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    parameters_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    code_version: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class FactorRunRecord(Base):
    __tablename__ = "factor_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("factor_definitions.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    config_json: Mapped[str] = mapped_column(Text)
    input_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    stock_pool: Mapped[str | None] = mapped_column(String(128), index=True)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    data_version: Mapped[str] = mapped_column(String(128), default="unknown")
    code_version: Mapped[str] = mapped_column(String(128), default="unknown")
    visible_time_field: Mapped[str] = mapped_column(String(128), default="visible_at")
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime)
    result_path: Mapped[str | None] = mapped_column(String(1024))
    error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class OptimizationTaskRecord(Base):
    __tablename__ = "optimization_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    method: Mapped[str] = mapped_column(String(32))
    config_json: Mapped[str] = mapped_column(Text)
    objective_names_json: Mapped[str] = mapped_column(Text, default="[]")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    total_trials: Mapped[int] = mapped_column(Integer, default=0)
    completed_trials: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    result_path: Mapped[str | None] = mapped_column(String(1024))
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class PortfolioRecord(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    method: Mapped[str] = mapped_column(String(64))
    constraints_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PortfolioTargetRecord(Base):
    __tablename__ = "portfolio_targets"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", "effective_at", name="uq_portfolio_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    target_weight: Mapped[float] = mapped_column(Float)
    target_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    effective_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class PaperAccountRecord(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    available_cash: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    frozen_cash: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class OmsOrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    strategy_release_id: Mapped[str | None] = mapped_column(String(64), index=True)
    signal_id: Mapped[str | None] = mapped_column(String(128), index=True)
    parent_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    average_fill_price: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(32), index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), default="paper", index=True)
    qmt_seq: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    qmt_order_id: Mapped[int | None] = mapped_column(Integer, index=True)
    order_sysid: Mapped[str | None] = mapped_column(String(64), index=True)
    order_remark: Mapped[str | None] = mapped_column(String(64), index=True)
    last_error: Mapped[str | None] = mapped_column(String(1024))
    source: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    data_origin: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    broker_reported_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class OmsTradeRecord(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("account_id", "broker_trade_id", name="uq_trade_broker_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    direction: Mapped[str] = mapped_column(String(8), default="unknown")
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    fees: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    transfer_fee: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    broker_reported_fee: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    fee_source: Mapped[str] = mapped_column(String(32), default="component_sum", index=True)
    fee_breakdown_complete: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), default="paper", index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), index=True)
    run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    data_origin: Mapped[str] = mapped_column(String(32), default="dyadtetrade", index=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    qmt_order_id: Mapped[int | None] = mapped_column(Integer, index=True)
    order_sysid: Mapped[str | None] = mapped_column(String(64), index=True)
    broker_trade_id: Mapped[str | None] = mapped_column(String(64), index=True)
    broker_reported_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)


class PositionRecord(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol", name="uq_account_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    available_quantity: Mapped[int] = mapped_column(Integer)
    frozen_quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    last_price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class BrokerSettlementRecord(Base):
    __tablename__ = "broker_settlements"
    __table_args__ = (
        UniqueConstraint("account_id", "settlement_id", name="uq_broker_settlement_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    settlement_id: Mapped[str] = mapped_column(String(128), index=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    broker_trade_id: Mapped[str | None] = mapped_column(String(64), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    notional: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    commission: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    transfer_fee: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal("0"))
    settlement_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    execution_mode: Mapped[str] = mapped_column(String(16), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), index=True)
    run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    data_origin: Mapped[str] = mapped_column(String(32), default="qmt_broker", index=True)
    broker_reported_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LedgerQuarantineRecord(Base):
    __tablename__ = "ledger_quarantine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    original_table: Mapped[str] = mapped_column(String(64))
    original_id: Mapped[str] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(String(256), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    quarantined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class RiskEventRecord(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(String(1024))
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class TradingRiskStateRecord(Base):
    __tablename__ = "trading_risk_states"
    __table_args__ = (
        UniqueConstraint("account_key", "execution_mode", name="uq_trading_risk_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String(64), index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(String(24), default="RECOVERY", index=True)
    reason: Mapped[str] = mapped_column(String(1024), default="startup")
    version: Mapped[int] = mapped_column(Integer, default=1)
    changed_by: Mapped[str] = mapped_column(String(128), default="system")
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class ExecutionUniverseReleaseRecord(Base):
    __tablename__ = "execution_universe_releases"
    __table_args__ = (UniqueConstraint("release_id", name="uq_execution_universe_release_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), index=True)
    scope: Mapped[str] = mapped_column(String(32), default="a_share", index=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    required_data_date: Mapped[str] = mapped_column(String(8))
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    rejection_counts_json: Mapped[str] = mapped_column(Text, default="{}")
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class ExecutionUniverseMemberRecord(Base):
    __tablename__ = "execution_universe_members"
    __table_args__ = (
        UniqueConstraint("release_id", "symbol", name="uq_execution_universe_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    exchange: Mapped[str] = mapped_column(String(8), index=True)
    asset_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    data_latest_date: Mapped[str] = mapped_column(String(8))
    quality_status: Mapped[str] = mapped_column(String(24), default="passed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class StrategyExecutionReleaseRecord(Base):
    __tablename__ = "strategy_execution_releases"
    __table_args__ = (UniqueConstraint("release_id", name="uq_strategy_execution_release_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), index=True)
    strategy_id: Mapped[str] = mapped_column(String(128), index=True)
    strategy_name: Mapped[str] = mapped_column(String(128), index=True)
    strategy_version: Mapped[str] = mapped_column(String(64))
    universe_release_id: Mapped[str] = mapped_column(String(64), index=True)
    parameters_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    max_order_notional: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    max_daily_notional: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    object_id: Mapped[str | None] = mapped_column(String(128), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
