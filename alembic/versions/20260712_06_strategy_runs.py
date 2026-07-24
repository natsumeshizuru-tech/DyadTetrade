"""Add recoverable strategy runtime state.

Revision ID: 20260712_06
Revises: 20260712_05
Create Date: 2026-07-12
"""

from collections.abc import Sequence

from alembic import op
from dyadtetrade.db import models  # noqa: F401
from dyadtetrade.db.session import Base

revision: str = "20260712_06"
down_revision: str | None = "20260712_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.tables["strategy_runs"].create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Runtime state remains available for audit and restart recovery.
    pass
