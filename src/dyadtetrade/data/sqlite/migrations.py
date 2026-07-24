from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from dyadtetrade.data.sqlite.connection import write_transaction

Migration = tuple[int, str, str]

CATALOG_MIGRATIONS: Sequence[Migration] = (
    (
        1,
        "initial_catalog",
        """
        CREATE TABLE instruments (
            symbol TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            market TEXT NOT NULL,
            name TEXT,
            instrument_type TEXT,
            list_date INTEGER,
            delist_date INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            price_tick REAL,
            volume_multiple REAL,
            raw_json TEXT,
            source TEXT NOT NULL DEFAULT 'xtdata',
            first_seen_at INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL
        );
        CREATE INDEX idx_instruments_market_active ON instruments(market, is_active);
        CREATE INDEX idx_instruments_lifecycle ON instruments(list_date, delist_date);

        CREATE TABLE trading_calendar (
            market TEXT NOT NULL,
            trade_date INTEGER NOT NULL,
            is_open INTEGER NOT NULL CHECK (is_open IN (0, 1)),
            prev_trade_date INTEGER,
            next_trade_date INTEGER,
            source TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (market, trade_date)
        );

        CREATE TABLE sector_snapshots (
            snapshot_date INTEGER NOT NULL,
            sector_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (snapshot_date, sector_name, symbol)
        );

        CREATE TABLE corporate_actions (
            symbol TEXT NOT NULL,
            action_date INTEGER NOT NULL,
            interest REAL NOT NULL DEFAULT 0,
            stock_bonus REAL NOT NULL DEFAULT 0,
            stock_gift REAL NOT NULL DEFAULT 0,
            allot_num REAL NOT NULL DEFAULT 0,
            allot_price REAL NOT NULL DEFAULT 0,
            gugai INTEGER NOT NULL DEFAULT 0,
            dr REAL NOT NULL DEFAULT 1,
            raw_json TEXT,
            source TEXT NOT NULL DEFAULT 'xtdata',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (symbol, action_date)
        );

        CREATE TABLE dataset_watermarks (
            dataset TEXT NOT NULL,
            period TEXT NOT NULL,
            symbol TEXT NOT NULL,
            first_ts INTEGER,
            last_ts INTEGER,
            row_count INTEGER NOT NULL DEFAULT 0,
            last_success_job_id TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (dataset, period, symbol)
        );

        CREATE TABLE job_runs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            requested_start INTEGER,
            requested_end INTEGER,
            effective_start INTEGER,
            effective_end INTEGER,
            status TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            finished_at INTEGER,
            total_items INTEGER NOT NULL DEFAULT 0,
            success_items INTEGER NOT NULL DEFAULT 0,
            failed_items INTEGER NOT NULL DEFAULT 0,
            inserted_rows INTEGER NOT NULL DEFAULT 0,
            updated_rows INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT,
            error_text TEXT
        );

        CREATE TABLE job_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES job_runs(job_id) ON DELETE CASCADE,
            dataset TEXT NOT NULL,
            period TEXT NOT NULL,
            symbol TEXT NOT NULL,
            requested_start INTEGER,
            requested_end INTEGER,
            effective_start INTEGER,
            effective_end INTEGER,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            inserted_rows INTEGER NOT NULL DEFAULT 0,
            updated_rows INTEGER NOT NULL DEFAULT 0,
            error_class TEXT,
            error_text TEXT,
            started_at INTEGER,
            finished_at INTEGER,
            UNIQUE(job_id, dataset, period, symbol, effective_start, effective_end)
        );
        CREATE INDEX idx_job_items_resume ON job_items(job_id, status, item_id);

        CREATE TABLE data_quality_issues (
            issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            severity TEXT NOT NULL,
            symbol TEXT,
            period TEXT,
            trade_date INTEGER,
            rule TEXT NOT NULL,
            details_json TEXT NOT NULL,
            job_id TEXT,
            shard TEXT,
            created_at INTEGER NOT NULL,
            resolved_at INTEGER
        );
        CREATE INDEX idx_quality_lookup ON data_quality_issues(trade_date, severity, rule);

        CREATE TABLE shard_manifest (
            shard_path TEXT PRIMARY KEY,
            dataset TEXT NOT NULL,
            market TEXT,
            period TEXT NOT NULL,
            start_date INTEGER,
            end_date INTEGER,
            row_count INTEGER NOT NULL DEFAULT 0,
            file_size INTEGER NOT NULL DEFAULT 0,
            schema_version INTEGER NOT NULL,
            writable INTEGER NOT NULL DEFAULT 1,
            checksum TEXT,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE archive_manifest (
            archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            archive_path TEXT NOT NULL UNIQUE,
            dataset TEXT NOT NULL,
            market TEXT,
            period TEXT NOT NULL,
            start_date INTEGER,
            end_date INTEGER,
            row_count INTEGER NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            verified_at INTEGER
        );

        CREATE TABLE process_locks (
            lock_name TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            acquired_at INTEGER NOT NULL,
            heartbeat_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        """,
    ),
    (
        2,
        "reference_data_snapshots",
        """
        CREATE TABLE sector_catalog (
            snapshot_date INTEGER NOT NULL,
            sector_name TEXT NOT NULL,
            total_member_count INTEGER NOT NULL,
            a_share_member_count INTEGER NOT NULL,
            source TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (snapshot_date, sector_name)
        ) WITHOUT ROWID;

        CREATE TABLE index_weight_catalog (
            snapshot_date INTEGER NOT NULL,
            index_code TEXT NOT NULL,
            total_component_count INTEGER NOT NULL,
            a_share_component_count INTEGER NOT NULL,
            total_weight REAL NOT NULL,
            source TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (snapshot_date, index_code)
        ) WITHOUT ROWID;

        CREATE TABLE index_weight_snapshots (
            snapshot_date INTEGER NOT NULL,
            index_code TEXT NOT NULL,
            symbol TEXT NOT NULL,
            weight REAL NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (snapshot_date, index_code, symbol)
        ) WITHOUT ROWID;
        CREATE INDEX idx_index_weight_symbol
            ON index_weight_snapshots(snapshot_date, symbol, index_code);
        """,
    ),
    (
        3,
        "remove_unverifiable_historical_sector_snapshots",
        """
        DELETE FROM sector_snapshots
        WHERE source = 'xtdata'
          AND snapshot_date NOT IN (
              SELECT snapshot_date FROM sector_catalog
          );
        """,
    ),
)

BAR_MIGRATIONS: Sequence[Migration] = (
    (
        1,
        "initial_bars",
        """
        CREATE TABLE bars (
            symbol TEXT NOT NULL,
            ts INTEGER NOT NULL,
            trade_date INTEGER NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            amount REAL,
            settlement_price REAL,
            open_interest REAL,
            pre_close REAL,
            suspend_flag INTEGER,
            source TEXT NOT NULL DEFAULT 'xtdata',
            ingested_at INTEGER NOT NULL,
            source_hash TEXT,
            PRIMARY KEY (symbol, ts)
        ) WITHOUT ROWID;
        CREATE INDEX idx_bars_trade_date_symbol ON bars(trade_date, symbol);
        CREATE INDEX idx_bars_ts ON bars(ts);
        """,
    ),
    (
        2,
        "drop_redundant_global_timestamp_index",
        """
        DROP INDEX IF EXISTS idx_bars_ts;
        """,
    ),
)

FUNDAMENTAL_MIGRATIONS: Sequence[Migration] = (
    (
        1,
        "initial_financial_records",
        """
        CREATE TABLE financial_records (
            symbol TEXT NOT NULL,
            table_name TEXT NOT NULL,
            report_date INTEGER NOT NULL,
            announcement_date INTEGER NOT NULL,
            row_ordinal INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            source TEXT NOT NULL,
            ingested_at INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            PRIMARY KEY (
                symbol, table_name, report_date, announcement_date, row_ordinal
            )
        ) WITHOUT ROWID;
        CREATE INDEX idx_financial_table_report
            ON financial_records(table_name, report_date, symbol);
        CREATE INDEX idx_financial_announcement
            ON financial_records(announcement_date, table_name, symbol);
        """,
    ),
)


def _apply(connection: sqlite3.Connection, migrations: Sequence[Migration]) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at INTEGER NOT NULL
        )
        """
    )
    applied = {int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")}
    for version, name, ddl in migrations:
        if version in applied:
            continue
        with write_transaction(connection):
            connection.executescript(ddl)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, CAST(unixepoch('now', 'subsec') * 1000 AS INTEGER))",
                (version, name),
            )


def migrate_catalog(connection: sqlite3.Connection) -> None:
    _apply(connection, CATALOG_MIGRATIONS)


def migrate_bars(connection: sqlite3.Connection) -> None:
    _apply(connection, BAR_MIGRATIONS)


def migrate_fundamentals(connection: sqlite3.Connection) -> None:
    _apply(connection, FUNDAMENTAL_MIGRATIONS)
