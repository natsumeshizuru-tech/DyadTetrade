"""align legacy quality issues with classified download failures

Revision ID: 20260713_22
Revises: 20260713_21
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_22"
down_revision: str | None = "20260713_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE quality_issues
            SET check_code = COALESCE(
                    (
                        SELECT item.failure_category
                        FROM download_task_items item
                        WHERE item.task_id = quality_issues.task_id
                          AND item.symbol = quality_issues.symbol
                          AND item.status = 'failed'
                        LIMIT 1
                    ),
                    check_code
                ),
                details_json = COALESCE(
                    (
                        SELECT item.failure_details_json
                        FROM download_task_items item
                        WHERE item.task_id = quality_issues.task_id
                          AND item.symbol = quality_issues.symbol
                          AND item.status = 'failed'
                        LIMIT 1
                    ),
                    details_json
                )
            WHERE check_code = 'download_failed'
              AND EXISTS (
                  SELECT 1
                  FROM download_task_items item
                  WHERE item.task_id = quality_issues.task_id
                    AND item.symbol = quality_issues.symbol
                    AND item.status = 'failed'
                    AND item.failure_category IS NOT NULL
              )
            """
        )
    )


def downgrade() -> None:
    # Classified quality evidence is intentionally retained.
    pass
