"""Align the strategy file name unique constraint and indexes.

Revision ID: 20260712_09
Revises: 20260712_08
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_09"
down_revision: str | None = "20260712_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    indexes = {
        str(index["name"]) for index in sa.inspect(op.get_bind()).get_indexes("strategy_files")
    }
    if "ix_strategy_files_name" in indexes:
        op.drop_index("ix_strategy_files_name", table_name="strategy_files")


def downgrade() -> None:
    # The table-level unique constraint continues to index names.
    pass
