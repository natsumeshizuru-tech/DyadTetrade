"""Add strategy file lifecycle and QMT simulation order mappings.

Revision ID: 20260712_08
Revises: 20260712_07
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_08"
down_revision: str | None = "20260712_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "strategy_files" not in tables:
        op.create_table(
            "strategy_files",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("relative_path", sa.String(256), nullable=False),
            sa.Column("draft_sha256", sa.String(64), nullable=False),
            sa.Column("validation_status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("smoke_status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("published_version", sa.Integer(), nullable=True),
            sa.Column("published_sha256", sa.String(64), nullable=True),
            sa.Column("runtime_status", sa.String(24), nullable=False, server_default="stopped"),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(1024), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name"),
            sa.UniqueConstraint("relative_path"),
        )
        for column in (
            "name",
            "draft_sha256",
            "validation_status",
            "smoke_status",
            "published_version",
            "published_sha256",
            "runtime_status",
            "heartbeat_at",
        ):
            op.create_index(f"ix_strategy_files_{column}", "strategy_files", [column])

    existing = {str(column["name"]) for column in inspector.get_columns("orders")}
    additions = {
        "execution_mode": sa.Column(
            "execution_mode", sa.String(16), nullable=False, server_default="paper"
        ),
        "qmt_seq": sa.Column("qmt_seq", sa.Integer(), nullable=True),
        "qmt_order_id": sa.Column("qmt_order_id", sa.Integer(), nullable=True),
        "order_sysid": sa.Column("order_sysid", sa.String(64), nullable=True),
        "order_remark": sa.Column("order_remark", sa.String(64), nullable=True),
        "last_error": sa.Column("last_error", sa.String(1024), nullable=True),
    }
    with op.batch_alter_table("orders") as batch:
        for name, column in additions.items():
            if name not in existing:
                batch.add_column(column)
    indexes = {str(index["name"]) for index in inspector.get_indexes("orders")}
    for column in (
        "execution_mode",
        "qmt_seq",
        "qmt_order_id",
        "order_sysid",
        "order_remark",
    ):
        name = f"ix_orders_{column}"
        if name not in indexes:
            op.create_index(name, "orders", [column], unique=column == "qmt_seq")


def downgrade() -> None:
    # Trading audit mappings are retained deliberately.
    pass
