"""preserve numeric QMT order id separately from order_sysid on broker trades

Revision ID: 20260713_20
Revises: 20260713_19
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_20"
down_revision: str | None = "20260713_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {str(item["name"]) for item in sa.inspect(bind).get_columns("trades")}
    with op.batch_alter_table("trades") as batch:
        if "qmt_order_id" not in columns:
            batch.add_column(sa.Column("qmt_order_id", sa.Integer()))
        if "order_sysid" not in columns:
            batch.add_column(sa.Column("order_sysid", sa.String(64)))

    existing_indexes = {
        str(item["name"])
        for item in sa.inspect(bind).get_indexes("trades")
        if item.get("name") is not None
    }
    for column in ("qmt_order_id", "order_sysid"):
        index_name = f"ix_trades_{column}"
        if index_name not in existing_indexes:
            op.create_index(index_name, "trades", [column])


def downgrade() -> None:
    # Broker identity evidence is intentionally retained.
    pass
