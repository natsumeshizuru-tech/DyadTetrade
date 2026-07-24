"""add automated strategy release lineage to OMS orders

Revision ID: 20260713_17
Revises: 20260713_16
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_17"
down_revision: str | None = "20260713_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names() -> set[str]:
    return {str(item["name"]) for item in sa.inspect(op.get_bind()).get_columns("orders")}


def _index_names() -> set[str]:
    return {
        str(item["name"])
        for item in sa.inspect(op.get_bind()).get_indexes("orders")
        if item.get("name") is not None
    }


def upgrade() -> None:
    columns = _column_names()
    if "strategy_release_id" not in columns:
        op.add_column("orders", sa.Column("strategy_release_id", sa.String(64)))
    if "signal_id" not in columns:
        op.add_column("orders", sa.Column("signal_id", sa.String(128)))
    indexes = _index_names()
    if "ix_orders_strategy_release_id" not in indexes:
        op.create_index("ix_orders_strategy_release_id", "orders", ["strategy_release_id"])
    if "ix_orders_signal_id" not in indexes:
        op.create_index("ix_orders_signal_id", "orders", ["signal_id"])


def downgrade() -> None:
    indexes = _index_names()
    if "ix_orders_signal_id" in indexes:
        op.drop_index("ix_orders_signal_id", table_name="orders")
    if "ix_orders_strategy_release_id" in indexes:
        op.drop_index("ix_orders_strategy_release_id", table_name="orders")
    columns = _column_names()
    if "signal_id" in columns:
        op.drop_column("orders", "signal_id")
    if "strategy_release_id" in columns:
        op.drop_column("orders", "strategy_release_id")
