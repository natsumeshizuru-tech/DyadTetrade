"""isolate broker ledger and add accounting precision metadata

Revision ID: 20260713_12
Revises: 20260713_11
Create Date: 2026-07-13
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_12"
down_revision: str | None = "20260713_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    instrument_columns = {str(item["name"]) for item in inspector.get_columns("instruments")}
    with op.batch_alter_table("instruments") as batch:
        for name, column in {
            "price_decimals": sa.Column(
                "price_decimals", sa.Integer(), nullable=False, server_default="2"
            ),
            "quantity_decimals": sa.Column(
                "quantity_decimals", sa.Integer(), nullable=False, server_default="0"
            ),
            "currency": sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
            "money_decimals": sa.Column(
                "money_decimals", sa.Integer(), nullable=False, server_default="2"
            ),
        }.items():
            if name not in instrument_columns:
                batch.add_column(column)
        batch.alter_column("price_tick", existing_type=sa.Float(), type_=sa.Numeric(18, 8))

    order_columns = {str(item["name"]) for item in inspector.get_columns("orders")}
    with op.batch_alter_table("orders") as batch:
        if "limit_price" not in order_columns:
            batch.add_column(sa.Column("limit_price", sa.Numeric(24, 8), nullable=True))
        if "average_fill_price" not in order_columns:
            batch.add_column(
                sa.Column(
                    "average_fill_price",
                    sa.Numeric(24, 8),
                    nullable=False,
                    server_default="0",
                )
            )

    trade_columns = {str(item["name"]) for item in inspector.get_columns("trades")}
    additions = {
        "direction": sa.Column("direction", sa.String(8), nullable=False, server_default="unknown"),
        "commission": sa.Column(
            "commission", sa.Numeric(24, 8), nullable=False, server_default="0"
        ),
        "tax": sa.Column("tax", sa.Numeric(24, 8), nullable=False, server_default="0"),
        "transfer_fee": sa.Column(
            "transfer_fee", sa.Numeric(24, 8), nullable=False, server_default="0"
        ),
        "execution_mode": sa.Column(
            "execution_mode", sa.String(16), nullable=False, server_default="unknown"
        ),
        "strategy_id": sa.Column("strategy_id", sa.String(128), nullable=True),
        "run_id": sa.Column("run_id", sa.String(128), nullable=True),
        "source": sa.Column("source", sa.String(32), nullable=False, server_default="unknown"),
        "is_simulated": sa.Column(
            "is_simulated", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        "broker_order_id": sa.Column("broker_order_id", sa.String(64), nullable=True),
        "broker_trade_id": sa.Column("broker_trade_id", sa.String(64), nullable=True),
        "broker_reported_at": sa.Column("broker_reported_at", sa.DateTime(), nullable=True),
    }
    with op.batch_alter_table("trades") as batch:
        for name, column in additions.items():
            if name not in trade_columns:
                batch.add_column(column)
        batch.alter_column("price", existing_type=sa.Float(), type_=sa.Numeric(24, 8))
        batch.alter_column("fees", existing_type=sa.Float(), type_=sa.Numeric(24, 8))

    with op.batch_alter_table("paper_accounts") as batch:
        for name in ("cash", "available_cash", "frozen_cash"):
            batch.alter_column(name, existing_type=sa.Float(), type_=sa.Numeric(24, 8))
    with op.batch_alter_table("positions") as batch:
        batch.alter_column("average_cost", existing_type=sa.Float(), type_=sa.Numeric(24, 8))
        batch.alter_column("last_price", existing_type=sa.Float(), type_=sa.Numeric(24, 8))

    op.create_table(
        "broker_settlements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("settlement_id", sa.String(128), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("broker_trade_id", sa.String(64), nullable=True),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("notional", sa.Numeric(24, 8), nullable=False),
        sa.Column("commission", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("tax", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("transfer_fee", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("settlement_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=True),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("broker_reported_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("account_id", "settlement_id", name="uq_broker_settlement_identity"),
    )
    op.create_table(
        "ledger_quarantine",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("original_table", sa.String(64), nullable=False),
        sa.Column("original_id", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "quarantined_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )

    rows = bind.execute(
        sa.text(
            "SELECT t.id, t.trade_id, t.order_id, t.account_id, t.symbol, t.quantity, "
            "t.price, t.fees, t.timestamp, o.execution_mode, o.strategy_id, o.direction "
            "FROM trades t LEFT JOIN orders o ON o.order_id=t.order_id"
        )
    ).mappings()
    unknown_ids: list[int] = []
    for row in rows:
        execution_mode = str(row["execution_mode"] or "")
        if row["account_id"] == "paper" or execution_mode == "paper":
            bind.execute(
                sa.text(
                    "UPDATE trades SET execution_mode='paper', source='paper_match', "
                    "is_simulated=1, strategy_id=:strategy_id, "
                    "direction=:direction, commission=fees "
                    "WHERE id=:id"
                ),
                {
                    "id": row["id"],
                    "strategy_id": row["strategy_id"],
                    "direction": row["direction"] or "unknown",
                },
            )
        elif execution_mode in {"qmt_sim", "qmt_live"} and row["trade_id"]:
            bind.execute(
                sa.text(
                    "UPDATE trades SET execution_mode=:mode, source='qmt_trade_query', "
                    "is_simulated=:simulated, strategy_id=:strategy_id, direction=:direction, "
                    "broker_order_id=order_id, broker_trade_id=trade_id, "
                    "broker_reported_at=timestamp "
                    "WHERE id=:id"
                ),
                {
                    "id": row["id"],
                    "mode": execution_mode,
                    "simulated": execution_mode == "qmt_sim",
                    "strategy_id": row["strategy_id"],
                    "direction": row["direction"] or "unknown",
                },
            )
        else:
            unknown_ids.append(int(row["id"]))
            bind.execute(
                sa.text(
                    "INSERT INTO ledger_quarantine "
                    "(entity_type,original_table,original_id,reason,payload_json) "
                    "VALUES ('trade','trades',:original_id,"
                    "'missing broker identity or provenance',:payload)"
                ),
                {
                    "original_id": str(row["id"]),
                    "payload": json.dumps(dict(row), default=str, ensure_ascii=False),
                },
            )
    if unknown_ids:
        bind.execute(
            sa.text("DELETE FROM trades WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": unknown_ids},
        )

    for table, columns in {
        "trades": (
            "execution_mode",
            "strategy_id",
            "run_id",
            "source",
            "is_simulated",
            "broker_order_id",
            "broker_trade_id",
            "broker_reported_at",
        ),
        "broker_settlements": (
            "account_id",
            "broker_trade_id",
            "broker_order_id",
            "symbol",
            "execution_mode",
            "strategy_id",
            "run_id",
            "is_simulated",
            "source",
            "broker_reported_at",
        ),
        "ledger_quarantine": ("entity_type", "original_id", "reason", "quarantined_at"),
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
    trade_constraints = {
        str(item["name"])
        for item in sa.inspect(bind).get_unique_constraints("trades")
        if item.get("name") is not None
    }
    if "uq_trade_broker_identity" not in trade_constraints:
        with op.batch_alter_table("trades") as batch:
            batch.create_unique_constraint(
                "uq_trade_broker_identity", ["account_id", "broker_trade_id"]
            )


def downgrade() -> None:
    # Ledger provenance and quarantined evidence are intentionally retained.
    pass
