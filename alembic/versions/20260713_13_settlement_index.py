"""index broker settlement identifiers

Revision ID: 20260713_13
Revises: 20260713_12
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_13"
down_revision: str | None = "20260713_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_broker_settlements_settlement_id",
        "broker_settlements",
        ["settlement_id"],
    )


def downgrade() -> None:
    # Settlement audit indexes are retained deliberately.
    pass
