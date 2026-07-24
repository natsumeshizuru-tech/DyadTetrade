"""repair A-share and ETF execution lot size metadata

Revision ID: 20260713_16
Revises: 20260713_15
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_16"
down_revision: str | None = "20260713_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    instruments = sa.table(
        "instruments",
        sa.column("asset_type", sa.String()),
        sa.column("lot_size", sa.Integer()),
    )
    op.execute(
        instruments.update()
        .where(instruments.c.asset_type.in_(("stock", "etf")))
        .values(lot_size=100)
    )


def downgrade() -> None:
    # The previous value came from XtData VolumeMultiple and was semantically
    # incorrect. A downgrade intentionally preserves the repaired metadata.
    pass
