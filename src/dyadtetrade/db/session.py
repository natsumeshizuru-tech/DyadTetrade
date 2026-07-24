from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from dyadtetrade.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.sqlite_url, future=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


async def initialize_database() -> None:
    from dyadtetrade.db import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        if connection.dialect.name == "sqlite":
            await _upgrade_sqlite_schema(connection)


async def _upgrade_sqlite_schema(connection: AsyncConnection) -> None:
    """Add columns introduced after the initial local-only prototype.

    SQLite has no lightweight ``ADD COLUMN IF NOT EXISTS`` on all supported
    versions.  The application therefore introspects the two original tables
    and performs additive, data-preserving upgrades.
    """
    upgrades: dict[str, dict[str, str]] = {
        "instruments": {
            "industry": "VARCHAR(128)",
            "list_date": "VARCHAR(8)",
            "delist_date": "VARCHAR(8)",
            "lot_size": "INTEGER",
            "price_tick": "FLOAT",
        },
        "data_files": {
            "checksum": "VARCHAR(64)",
            "schema_version": "VARCHAR(16) NOT NULL DEFAULT '1'",
            "pipeline_version": "VARCHAR(32) NOT NULL DEFAULT 'market-v1'",
            "imported_from": "VARCHAR(1024)",
        },
        "download_tasks": {
            "idempotency_key": "VARCHAR(64)",
            "symbols_json": "TEXT NOT NULL DEFAULT '[]'",
            "adjust": "VARCHAR(16) NOT NULL DEFAULT 'front'",
            "total_items": "INTEGER NOT NULL DEFAULT 0",
            "completed_items": "INTEGER NOT NULL DEFAULT 0",
            "succeeded_items": "INTEGER NOT NULL DEFAULT 0",
            "failed_items": "INTEGER NOT NULL DEFAULT 0",
            "progress": "FLOAT NOT NULL DEFAULT 0",
            "max_retries": "INTEGER NOT NULL DEFAULT 2",
            "quality_report_json": "VARCHAR(1024)",
            "quality_report_csv": "VARCHAR(1024)",
            "started_at": "DATETIME",
            "finished_at": "DATETIME",
        },
        "download_task_items": {
            "failure_category": "VARCHAR(32)",
            "failure_details_json": "TEXT NOT NULL DEFAULT '{}'",
            "retryable": "BOOLEAN NOT NULL DEFAULT 1",
            "next_retry_at": "DATETIME",
            "quarantined_at": "DATETIME",
        },
    }
    for table, columns in upgrades.items():
        result = await connection.execute(text(f"PRAGMA table_info({table})"))
        existing = {str(row[1]) for row in result.fetchall()}
        for name, declaration in columns.items():
            if name not in existing:
                await connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                )
    await connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_download_tasks_idempotency_key "
            "ON download_tasks (idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
    )
    for column in ("failure_category", "retryable", "next_retry_at", "quarantined_at"):
        await connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_download_task_items_{column} "
                f"ON download_task_items ({column})"
            )
        )
