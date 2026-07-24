"""Persist strategy, benchmark and relative net value curves.

Revision ID: 20260712_07
Revises: 20260712_06
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_07"
down_revision: str | None = "20260712_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {str(column["name"]) for column in inspector.get_columns("backtest_daily_equity")}
    additions = {
        "net_value": sa.Column("net_value", sa.Float(), nullable=False, server_default="1"),
        "benchmark_net_value": sa.Column("benchmark_net_value", sa.Float(), nullable=True),
        "relative_net_value": sa.Column("relative_net_value", sa.Float(), nullable=True),
    }
    with op.batch_alter_table("backtest_daily_equity") as batch:
        for name, column in additions.items():
            if name not in existing:
                batch.add_column(column)


def downgrade() -> None:
    # Historical benchmark alignment is retained for reproducibility.
    pass
