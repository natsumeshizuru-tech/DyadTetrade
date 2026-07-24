"""Add the legacy instrument industry lookup index.

Revision ID: 20260712_02
Revises: 20260712_01
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260712_02"
down_revision: str | None = "20260712_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_instruments_industry ON instruments (industry)")


def downgrade() -> None:
    # Keep the data-preserving policy used by the initial local migration.
    pass
