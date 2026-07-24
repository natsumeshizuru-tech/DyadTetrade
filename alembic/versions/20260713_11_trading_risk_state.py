"""persist trading risk operating state

Revision ID: 20260713_11
Revises: 20260712_10
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_11"
down_revision: str | None = "20260712_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_risk_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="RECOVERY"),
        sa.Column("reason", sa.String(1024), nullable=False, server_default="startup"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("changed_by", sa.String(128), nullable=False, server_default="system"),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_key", "execution_mode", name="uq_trading_risk_state"),
    )
    op.create_index("ix_trading_risk_states_account_key", "trading_risk_states", ["account_key"])
    op.create_index(
        "ix_trading_risk_states_execution_mode", "trading_risk_states", ["execution_mode"]
    )
    op.create_index("ix_trading_risk_states_state", "trading_risk_states", ["state"])
    op.create_index("ix_trading_risk_states_changed_at", "trading_risk_states", ["changed_at"])


def downgrade() -> None:
    # Safety/audit state is intentionally retained; destructive downgrade requires manual review.
    pass
