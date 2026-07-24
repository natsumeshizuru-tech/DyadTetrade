"""use decimal storage for backtest and portfolio accounting values

Revision ID: 20260713_14
Revises: 20260713_13
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_14"
down_revision: str | None = "20260713_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backtest_orders") as batch:
        batch.alter_column(
            "average_fill_price",
            existing_type=sa.Float(),
            type_=sa.Numeric(24, 8),
            existing_nullable=False,
        )
    with op.batch_alter_table("backtest_trades") as batch:
        for column in ("price", "commission", "tax", "transfer_fee"):
            batch.alter_column(
                column,
                existing_type=sa.Float(),
                type_=sa.Numeric(24, 8),
                existing_nullable=False,
            )
    with op.batch_alter_table("backtest_daily_equity") as batch:
        for column in ("cash", "positions_value", "total_assets"):
            batch.alter_column(
                column,
                existing_type=sa.Float(),
                type_=sa.Numeric(24, 8),
                existing_nullable=False,
            )
    with op.batch_alter_table("portfolio_targets") as batch:
        batch.alter_column(
            "target_value",
            existing_type=sa.Float(),
            type_=sa.Numeric(24, 8),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Precision upgrades are deliberately retained to avoid lossy conversion.
    pass
