"""Add recoverable optimization task progress and results.

Revision ID: 20260712_04
Revises: 20260712_03
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_04"
down_revision: str | None = "20260712_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {str(column["name"]) for column in inspector.get_columns("optimization_tasks")}
    additions = {
        "objective_names_json": sa.Column(
            "objective_names_json", sa.Text(), nullable=False, server_default="[]"
        ),
        "total_trials": sa.Column("total_trials", sa.Integer(), nullable=False, server_default="0"),
        "completed_trials": sa.Column(
            "completed_trials", sa.Integer(), nullable=False, server_default="0"
        ),
        "cancel_requested": sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "result_json": sa.Column("result_json", sa.Text(), nullable=True),
        "error_message": sa.Column("error_message", sa.String(length=2048), nullable=True),
        "started_at": sa.Column("started_at", sa.DateTime(), nullable=True),
        "finished_at": sa.Column("finished_at", sa.DateTime(), nullable=True),
    }
    with op.batch_alter_table("optimization_tasks") as batch:
        for name, column in additions.items():
            if name not in existing:
                batch.add_column(column)


def downgrade() -> None:
    # Optimization evidence is retained for reproducibility and audit.
    pass
