from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from dyadtetrade.db.migration_url import to_sync_database_url

ROOT = Path(__file__).parents[1]


def test_sqlite_async_url_is_converted_for_alembic() -> None:
    assert (
        to_sync_database_url("sqlite+aiosqlite:///./temporary/dyadtetrade-test.db")
        == "sqlite:///./temporary/dyadtetrade-test.db"
    )


def test_postgres_async_url_preserves_credentials_and_database() -> None:
    assert (
        to_sync_database_url("postgresql+asyncpg://user:secret@localhost:5432/dyadtetrade")
        == "postgresql+psycopg://user:secret@localhost:5432/dyadtetrade"
    )


def test_alembic_rebuilds_empty_database_to_head(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    database_url_path = database.as_posix()
    if not database_url_path.startswith("/"):
        database_url_path = f"/{database_url_path}"
    environment = {
        **os.environ,
        "DYADTETRADE_SQLITE_URL": f"sqlite+aiosqlite://{database_url_path}",
    }

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        trade_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(trades)").fetchall()
        }
        settlement_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(broker_settlements)").fetchall()
        }
        download_item_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(download_task_items)").fetchall()
        }

    assert revision == ("20260713_22",)
    assert {"instruments", "download_tasks", "orders", "broker_settlements"} <= tables
    assert {
        "broker_reported_fee",
        "fee_source",
        "fee_breakdown_complete",
        "data_origin",
        "qmt_order_id",
        "order_sysid",
    } <= trade_columns
    assert "data_origin" in settlement_columns
    assert {
        "failure_category",
        "failure_details_json",
        "retryable",
        "next_retry_at",
        "quarantined_at",
    } <= download_item_columns
