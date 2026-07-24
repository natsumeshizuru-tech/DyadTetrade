"""classify market-data download failures and persist retry policy

Revision ID: 20260713_21
Revises: 20260713_20
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260713_21"
down_revision: str | None = "20260713_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {str(item["name"]) for item in sa.inspect(bind).get_columns("download_task_items")}
    with op.batch_alter_table("download_task_items") as batch:
        if "failure_category" not in columns:
            batch.add_column(sa.Column("failure_category", sa.String(32)))
        if "failure_details_json" not in columns:
            batch.add_column(
                sa.Column("failure_details_json", sa.Text(), nullable=False, server_default="{}")
            )
        if "retryable" not in columns:
            batch.add_column(
                sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.true())
            )
        if "next_retry_at" not in columns:
            batch.add_column(sa.Column("next_retry_at", sa.DateTime()))
        if "quarantined_at" not in columns:
            batch.add_column(sa.Column("quarantined_at", sa.DateTime()))

    _classify_existing_failures(bind)
    existing_indexes = {
        str(item["name"])
        for item in sa.inspect(bind).get_indexes("download_task_items")
        if item.get("name") is not None
    }
    for column in ("failure_category", "retryable", "next_retry_at", "quarantined_at"):
        index_name = f"ix_download_task_items_{column}"
        if index_name not in existing_indexes:
            op.create_index(index_name, "download_task_items", [column])


def _classify_existing_failures(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE download_task_items
            SET failure_category = CASE
                WHEN error_message LIKE '%负成交%' OR error_message LIKE '%非正数 OHLC%'
                     OR error_message LIKE '%high 小于%' OR error_message LIKE '%low 大于%'
                    THEN 'quality_rejected'
                WHEN error_message LIKE '%返回空行情%' THEN 'no_market_data'
                ELSE 'provider_error'
            END,
            failure_details_json = CASE
                WHEN error_message LIKE '%负成交%' OR error_message LIKE '%非正数 OHLC%'
                     OR error_message LIKE '%high 小于%' OR error_message LIKE '%low 大于%'
                    THEN '{"source":"legacy_migration","reason":"quality_gate"}'
                WHEN error_message LIKE '%返回空行情%'
                    THEN '{"source":"legacy_migration","reason":"provider_returned_no_rows"}'
                ELSE '{"source":"legacy_migration","reason":"provider_exception"}'
            END,
            retryable = CASE
                WHEN error_message LIKE '%返回空行情%' OR error_message LIKE '%负成交%'
                     OR error_message LIKE '%非正数 OHLC%' OR error_message LIKE '%high 小于%'
                     OR error_message LIKE '%low 大于%'
                    THEN 0 ELSE 1
            END,
            quarantined_at = CASE
                WHEN error_message LIKE '%返回空行情%' OR error_message LIKE '%负成交%'
                     OR error_message LIKE '%非正数 OHLC%' OR error_message LIKE '%high 小于%'
                     OR error_message LIKE '%low 大于%'
                    THEN COALESCE(finished_at, updated_at) ELSE NULL
            END
            WHERE status = 'failed'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE download_task_items
            SET failure_category = 'unsupported_instrument', retryable = 0,
                failure_details_json = '{"source":"migration","reason":"pseudo_name"}',
                quarantined_at = COALESCE(finished_at, updated_at)
            WHERE status = 'failed' AND symbol IN (
                SELECT symbol FROM instruments
                WHERE name LIKE '%认购款%' OR name LIKE '%申购款%' OR name LIKE '%赎回款%'
            )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE download_task_items
            SET failure_category = 'unsupported_instrument', retryable = 0,
                failure_details_json = '{"source":"migration","reason":"bond_in_stock_universe"}',
                quarantined_at = COALESCE(finished_at, updated_at)
            WHERE status = 'failed' AND symbol IN (
                SELECT i.symbol FROM instruments i JOIN download_tasks t
                WHERE i.exchange = 'BJ' AND i.asset_type = 'stock'
                  AND (i.symbol LIKE '8%.BJ' OR i.symbol LIKE '9%.BJ')
                  AND i.delist_date IS NOT NULL AND i.delist_date > t.end_date
                  AND t.id = download_task_items.task_id
            )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE download_task_items
            SET failure_category = 'delisted', retryable = 0,
                failure_details_json = '{"source":"migration","reason":"missing_catalog"}',
                quarantined_at = COALESCE(finished_at, updated_at)
            WHERE status = 'failed' AND failure_category = 'quality_rejected'
              AND NOT EXISTS (
                  SELECT 1 FROM instruments i WHERE i.symbol = download_task_items.symbol
              )
            """
        )
    )


def downgrade() -> None:
    # Failure evidence and quarantine decisions are intentionally retained.
    pass
