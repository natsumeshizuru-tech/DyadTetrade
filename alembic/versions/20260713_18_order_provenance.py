"""add OMS order provenance and broker observation timestamps

Revision ID: 20260713_18
Revises: 20260713_17
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_18"
down_revision: str | None = "20260713_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {str(item["name"]) for item in sa.inspect(bind).get_columns("orders")}
    additions = {
        "run_id": sa.Column("run_id", sa.String(128)),
        "source": sa.Column("source", sa.String(32), nullable=False, server_default="unknown"),
        "data_origin": sa.Column(
            "data_origin", sa.String(32), nullable=False, server_default="unknown"
        ),
        "is_simulated": sa.Column(
            "is_simulated", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        "submitted_at": sa.Column("submitted_at", sa.DateTime()),
        "broker_reported_at": sa.Column("broker_reported_at", sa.DateTime()),
    }
    with op.batch_alter_table("orders") as batch:
        for name, column in additions.items():
            if name not in columns:
                batch.add_column(column)

    bind.execute(
        sa.text(
            "UPDATE orders SET "
            "source=CASE WHEN execution_mode='paper' THEN 'paper_order' "
            "ELSE 'qmt_order_submit' END, "
            "data_origin='dyadtetrade', "
            "is_simulated=CASE WHEN execution_mode='qmt_live' THEN 0 ELSE 1 END, "
            "submitted_at=CASE WHEN qmt_seq IS NOT NULL THEN created_at ELSE submitted_at END "
            "WHERE source='unknown' OR data_origin='unknown'"
        )
    )

    existing_indexes = {
        str(item["name"])
        for item in sa.inspect(bind).get_indexes("orders")
        if item.get("name") is not None
    }
    for column in ("run_id", "source", "data_origin", "is_simulated", "broker_reported_at"):
        index_name = f"ix_orders_{column}"
        if index_name not in existing_indexes:
            op.create_index(index_name, "orders", [column])


def downgrade() -> None:
    # Provenance is required for ledger audit and is intentionally retained.
    pass
