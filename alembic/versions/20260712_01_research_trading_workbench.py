"""Add research, backtest, portfolio, paper, OMS, risk and audit metadata tables.

Revision ID: 20260712_01
Revises: None
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
from dyadtetrade.db import models  # noqa: F401
from dyadtetrade.db.session import Base

revision: str = "20260712_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = (
    "instruments",
    "trading_days",
    "data_files",
    "download_tasks",
    "download_task_items",
    "sectors",
    "sector_members",
    "stock_pools",
    "stock_pool_members",
    "quality_issues",
    "strategies",
    "strategy_versions",
    "backtest_tasks",
    "backtest_results",
    "backtest_orders",
    "backtest_trades",
    "backtest_daily_equity",
    "factor_runs",
    "optimization_tasks",
    "portfolios",
    "portfolio_targets",
    "paper_accounts",
    "orders",
    "trades",
    "positions",
    "risk_events",
    "audit_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in NEW_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Business and audit data is deliberately not deleted automatically.
    # A destructive downgrade requires an explicit, reviewed archival migration.
    pass
