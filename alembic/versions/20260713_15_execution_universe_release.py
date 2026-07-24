"""add dynamic execution universe and automated strategy release tables

Revision ID: 20260713_15
Revises: 20260713_14
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_15"
down_revision: str | None = "20260713_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_index(
    table_name: str, index_name: str, columns: list[str], *, unique: bool = False
) -> None:
    existing_indexes = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
        if item.get("name") is not None
    }
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "execution_universe_releases" not in existing_tables:
        op.create_table(
            "execution_universe_releases",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("release_id", sa.String(64), nullable=False),
            sa.Column("trade_date", sa.String(8), nullable=False),
            sa.Column("execution_mode", sa.String(16), nullable=False),
            sa.Column("scope", sa.String(32), nullable=False, server_default="a_share"),
            sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("symbol_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("snapshot_hash", sa.String(64), nullable=False),
            sa.Column("required_data_date", sa.String(8), nullable=False),
            sa.Column("filters_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("rejection_counts_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("reviewed_by", sa.String(128)),
            sa.Column("approved_by", sa.String(128)),
            sa.Column("reviewed_at", sa.DateTime()),
            sa.Column("activated_at", sa.DateTime()),
            sa.Column("expires_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("release_id", name="uq_execution_universe_release_id"),
        )
    for column in ("release_id", "trade_date", "execution_mode", "scope", "status"):
        _ensure_index(
            "execution_universe_releases",
            f"ix_execution_universe_releases_{column}",
            [column],
            unique=column == "release_id",
        )
    _ensure_index(
        "execution_universe_releases",
        "ix_execution_universe_releases_snapshot_hash",
        ["snapshot_hash"],
    )
    _ensure_index(
        "execution_universe_releases",
        "ix_execution_universe_releases_expires_at",
        ["expires_at"],
    )
    _ensure_index(
        "execution_universe_releases",
        "ix_execution_universe_releases_created_at",
        ["created_at"],
    )

    if "execution_universe_members" not in existing_tables:
        op.create_table(
            "execution_universe_members",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("release_id", sa.String(64), nullable=False),
            sa.Column("symbol", sa.String(16), nullable=False),
            sa.Column("exchange", sa.String(8), nullable=False),
            sa.Column("asset_type", sa.String(32), nullable=False),
            sa.Column("name", sa.String(128)),
            sa.Column("data_latest_date", sa.String(8), nullable=False),
            sa.Column("quality_status", sa.String(24), nullable=False, server_default="passed"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("release_id", "symbol", name="uq_execution_universe_member"),
        )
    for column in ("release_id", "symbol", "exchange", "asset_type", "quality_status"):
        _ensure_index(
            "execution_universe_members",
            f"ix_execution_universe_members_{column}",
            [column],
        )

    if "strategy_execution_releases" not in existing_tables:
        op.create_table(
            "strategy_execution_releases",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("release_id", sa.String(64), nullable=False),
            sa.Column("trade_date", sa.String(8), nullable=False),
            sa.Column("execution_mode", sa.String(16), nullable=False),
            sa.Column("strategy_id", sa.String(128), nullable=False),
            sa.Column("strategy_name", sa.String(128), nullable=False),
            sa.Column("strategy_version", sa.String(64), nullable=False),
            sa.Column("universe_release_id", sa.String(64), nullable=False),
            sa.Column("parameters_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("max_order_notional", sa.Numeric(24, 8), nullable=False),
            sa.Column("max_daily_notional", sa.Numeric(24, 8), nullable=False),
            sa.Column("approved_by", sa.String(128)),
            sa.Column("activated_at", sa.DateTime()),
            sa.Column("expires_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("release_id", name="uq_strategy_execution_release_id"),
        )
    for column in (
        "release_id",
        "trade_date",
        "execution_mode",
        "strategy_id",
        "strategy_name",
        "universe_release_id",
        "status",
        "expires_at",
        "created_at",
    ):
        _ensure_index(
            "strategy_execution_releases",
            f"ix_strategy_execution_releases_{column}",
            [column],
            unique=column == "release_id",
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (
        "strategy_execution_releases",
        "execution_universe_members",
        "execution_universe_releases",
    ):
        if table in existing_tables:
            op.drop_table(table)
