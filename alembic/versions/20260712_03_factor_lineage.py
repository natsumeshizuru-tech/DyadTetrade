"""Add factor definitions and reproducible factor-run lineage.

Revision ID: 20260712_03
Revises: 20260712_02
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from dyadtetrade.db import models  # noqa: F401
from dyadtetrade.db.session import Base

revision: str = "20260712_03"
down_revision: str | None = "20260712_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]) for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["factor_definitions"].create(bind=bind, checkfirst=True)
    inspector = sa.inspect(bind)
    existing = _column_names("factor_runs")
    additions = {
        "idempotency_key": sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        "definition_id": sa.Column("definition_id", sa.Integer(), nullable=True),
        "progress": sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        "input_fields_json": sa.Column(
            "input_fields_json", sa.Text(), nullable=False, server_default="[]"
        ),
        "stock_pool": sa.Column("stock_pool", sa.String(length=128), nullable=True),
        "parameters_json": sa.Column(
            "parameters_json", sa.Text(), nullable=False, server_default="{}"
        ),
        "data_version": sa.Column(
            "data_version", sa.String(length=128), nullable=False, server_default="unknown"
        ),
        "code_version": sa.Column(
            "code_version", sa.String(length=128), nullable=False, server_default="unknown"
        ),
        "visible_time_field": sa.Column(
            "visible_time_field",
            sa.String(length=128),
            nullable=False,
            server_default="visible_at",
        ),
        "calculated_at": sa.Column("calculated_at", sa.DateTime(), nullable=True),
        "error_message": sa.Column("error_message", sa.String(length=2048), nullable=True),
        "finished_at": sa.Column("finished_at", sa.DateTime(), nullable=True),
    }
    with op.batch_alter_table("factor_runs") as batch:
        for name, column in additions.items():
            if name not in existing:
                batch.add_column(column)
    indexes = {str(item["name"]) for item in inspector.get_indexes("factor_runs")}
    foreign_columns = {
        tuple(str(column) for column in item.get("constrained_columns", []))
        for item in inspector.get_foreign_keys("factor_runs")
    }
    with op.batch_alter_table("factor_runs") as batch:
        if "ix_factor_runs_idempotency_key" not in indexes:
            batch.create_index("ix_factor_runs_idempotency_key", ["idempotency_key"], unique=True)
        if "ix_factor_runs_definition_id" not in indexes:
            batch.create_index("ix_factor_runs_definition_id", ["definition_id"], unique=False)
        if "ix_factor_runs_stock_pool" not in indexes:
            batch.create_index("ix_factor_runs_stock_pool", ["stock_pool"], unique=False)
        if ("definition_id",) not in foreign_columns:
            batch.create_foreign_key(
                "fk_factor_runs_definition_id_factor_definitions",
                "factor_definitions",
                ["definition_id"],
                ["id"],
            )


def downgrade() -> None:
    # Factor lineage is audit evidence and is never removed automatically.
    pass
