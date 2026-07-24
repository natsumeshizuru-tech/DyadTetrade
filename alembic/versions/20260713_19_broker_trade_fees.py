"""preserve broker-reported aggregate trade fees and ledger provenance

Revision ID: 20260713_19
Revises: 20260713_18
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_19"
down_revision: str | None = "20260713_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    trade_columns = {str(item["name"]) for item in inspector.get_columns("trades")}
    trade_additions = {
        "broker_reported_fee": sa.Column("broker_reported_fee", sa.Numeric(24, 8)),
        "fee_source": sa.Column(
            "fee_source", sa.String(32), nullable=False, server_default="component_sum"
        ),
        "fee_breakdown_complete": sa.Column(
            "fee_breakdown_complete", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        "data_origin": sa.Column(
            "data_origin", sa.String(32), nullable=False, server_default="dyadtetrade"
        ),
    }
    with op.batch_alter_table("trades") as batch:
        for name, column in trade_additions.items():
            if name not in trade_columns:
                batch.add_column(column)

    bind.execute(
        sa.text(
            "UPDATE trades SET "
            "data_origin=CASE WHEN execution_mode IN ('qmt_sim','qmt_live') "
            "THEN 'qmt_broker' ELSE 'dyadtetrade' END, "
            "fee_source=CASE WHEN execution_mode IN ('qmt_sim','qmt_live') "
            "THEN 'unavailable' ELSE 'component_sum' END, "
            "fee_breakdown_complete=CASE WHEN execution_mode IN ('qmt_sim','qmt_live') "
            "THEN 0 ELSE 1 END"
        )
    )

    settlement_columns = {
        str(item["name"]) for item in sa.inspect(bind).get_columns("broker_settlements")
    }
    if "data_origin" not in settlement_columns:
        with op.batch_alter_table("broker_settlements") as batch:
            batch.add_column(
                sa.Column(
                    "data_origin",
                    sa.String(32),
                    nullable=False,
                    server_default="qmt_broker",
                )
            )

    for table, columns in {
        "trades": ("fee_source", "fee_breakdown_complete", "data_origin"),
        "broker_settlements": ("data_origin",),
    }.items():
        existing_indexes = {
            str(item["name"])
            for item in sa.inspect(bind).get_indexes(table)
            if item.get("name") is not None
        }
        for column in columns:
            index_name = f"ix_{table}_{column}"
            if index_name not in existing_indexes:
                op.create_index(index_name, table, [column])


def downgrade() -> None:
    # Broker fee provenance is accounting evidence and is intentionally retained.
    pass
