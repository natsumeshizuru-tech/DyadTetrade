"""Restore the explicit strategy file lookup index.

Revision ID: 20260712_10
Revises: 20260712_09
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_10"
down_revision: str | None = "20260712_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    indexes = {
        str(index["name"]) for index in sa.inspect(op.get_bind()).get_indexes("strategy_files")
    }
    if "ix_strategy_files_name" not in indexes:
        op.create_index("ix_strategy_files_name", "strategy_files", ["name"], unique=True)


def downgrade() -> None:
    # Name lookup remains protected by the table unique constraint.
    pass
