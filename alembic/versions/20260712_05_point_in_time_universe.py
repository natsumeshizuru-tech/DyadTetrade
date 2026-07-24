"""Add point-in-time sector and stock-pool membership snapshots.

Revision ID: 20260712_05
Revises: 20260712_04
Create Date: 2026-07-12
"""

from collections.abc import Sequence
from datetime import datetime

from alembic import op
from dyadtetrade.db import models  # noqa: F401
from dyadtetrade.db.session import Base

revision: str = "20260712_05"
down_revision: str | None = "20260712_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("sector_member_snapshots", "stock_pool_member_snapshots"):
        Base.metadata.tables[table].create(bind=bind, checkfirst=True)
    sector_members = Base.metadata.tables["sector_members"]
    sector_snapshots = Base.metadata.tables["sector_member_snapshots"]
    snapshot_date = datetime.now().strftime("%Y%m%d")
    existing_sector_rows = {
        (int(row.sector_id), str(row.symbol), str(row.as_of_date))
        for row in bind.execute(
            sector_snapshots.select().with_only_columns(
                sector_snapshots.c.sector_id,
                sector_snapshots.c.symbol,
                sector_snapshots.c.as_of_date,
            )
        )
    }
    sector_payload = [
        {
            "sector_id": int(row.sector_id),
            "symbol": str(row.symbol),
            "as_of_date": snapshot_date,
            "source": "migration-current-snapshot",
        }
        for row in bind.execute(
            sector_members.select().with_only_columns(
                sector_members.c.sector_id, sector_members.c.symbol
            )
        )
        if (int(row.sector_id), str(row.symbol), snapshot_date) not in existing_sector_rows
    ]
    if sector_payload:
        bind.execute(sector_snapshots.insert(), sector_payload)

    pool_members = Base.metadata.tables["stock_pool_members"]
    pool_snapshots = Base.metadata.tables["stock_pool_member_snapshots"]
    existing_pool_rows = {
        (int(row.pool_id), str(row.symbol), str(row.effective_from))
        for row in bind.execute(
            pool_snapshots.select().with_only_columns(
                pool_snapshots.c.pool_id,
                pool_snapshots.c.symbol,
                pool_snapshots.c.effective_from,
            )
        )
    }
    pool_payload = []
    for row in bind.execute(
        pool_members.select().with_only_columns(
            pool_members.c.pool_id,
            pool_members.c.symbol,
            pool_members.c.added_at,
        )
    ):
        effective_from = row.added_at.strftime("%Y%m%d")
        key = (int(row.pool_id), str(row.symbol), effective_from)
        if key not in existing_pool_rows:
            pool_payload.append(
                {
                    "pool_id": key[0],
                    "symbol": key[1],
                    "effective_from": effective_from,
                    "source": "migration-current-snapshot",
                }
            )
    if pool_payload:
        bind.execute(pool_snapshots.insert(), pool_payload)


def downgrade() -> None:
    # Point-in-time membership is research lineage and is retained automatically.
    pass
