from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dyadtetrade.data.config import StorageConfig, load_data_config
from dyadtetrade.data.models import BarRecord, InstrumentRecord, QualityIssueRecord
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository, ShardManager

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def timestamp_ms(value: str) -> int:
    local = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ)
    return int(local.timestamp() * 1000)


def bar(symbol: str, value: str, close: float, *, source_hash: str) -> BarRecord:
    return BarRecord(
        symbol=symbol,
        ts=timestamp_ms(value),
        trade_date=int(value[:8]),
        open=close - 0.1,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        volume=1000,
        amount=close * 1000,
        pre_close=close - 0.1,
        suspend_flag=0,
        ingested_at=timestamp_ms("20260718010000"),
        source_hash=source_hash,
    )


def test_typed_config_supports_nested_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "market_data.yaml"
    config.write_text("storage:\n  root: original\n", encoding="utf-8")
    monkeypatch.setenv("DYADTETRADE_DATA__STORAGE__ROOT", str(tmp_path / "db"))

    settings = load_data_config(config)

    assert settings.storage.root == tmp_path / "db"
    assert settings.backfill.periods == ["1d", "1m"]
    assert settings.backfill.enable_tick is False


def test_typed_config_merges_ignored_local_override(tmp_path: Path) -> None:
    config = tmp_path / "market_data.yaml"
    config.write_text("storage:\n  root: public-default\n", encoding="utf-8")
    local = tmp_path / "market_data.local.yaml"
    local.write_text("storage:\n  root: private-local\n", encoding="utf-8")

    settings = load_data_config(config)

    assert settings.storage.root == Path("private-local")


def test_catalog_migration_and_calendar_clipping_are_idempotent(tmp_path: Path) -> None:
    storage = StorageConfig(root=tmp_path)
    repository = CatalogRepository(storage)
    repository.initialize()
    repository.initialize()
    repository.upsert_calendar(
        "SH",
        [20260713, 20260714, 20260715, 20260716, 20260717],
        source="test-calendar",
    )

    assert repository.clip_to_open_days("SH", 20240101, 20260718) == (20260713, 20260717)
    with sqlite3.connect(repository.path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "instruments",
            "trading_calendar",
            "corporate_actions",
            "dataset_watermarks",
            "job_runs",
            "job_items",
            "data_quality_issues",
            "shard_manifest",
            "archive_manifest",
            "process_locks",
        } <= tables
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (3,)


def test_completed_scope_checkpoint_requires_a_spanning_success(tmp_path: Path) -> None:
    repository = CatalogRepository(StorageConfig(root=tmp_path))
    repository.initialize()
    repository.create_job(
        "financial-short",
        "financial-backfill",
        requested_start=20240101,
        requested_end=20260717,
        effective_start=20240101,
        effective_end=20260717,
    )
    short = repository.start_job_item(
        "financial-short",
        dataset="fundamentals",
        period="financial",
        symbol="600000.SH",
        requested_start=20240101,
        requested_end=20260717,
        effective_start=20240101,
        effective_end=20260717,
    )
    repository.finish_job_item(short, status="completed")

    assert (
        repository.completed_symbols_for_scope(
            dataset="fundamentals", period="financial", start=20100101, end=20260717
        )
        == set()
    )
    assert repository.completed_symbols_for_scope(
        dataset="fundamentals", period="financial", start=20240101, end=20260717
    ) == {"600000.SH"}


def test_stock_scope_includes_delisted_and_excludes_future_symbols(tmp_path: Path) -> None:
    repository = CatalogRepository(StorageConfig(root=tmp_path))
    repository.initialize()

    def instrument(
        symbol: str, list_date: int, delist_date: int | None, *, active: bool
    ) -> InstrumentRecord:
        code, market = symbol.split(".")
        return InstrumentRecord(
            symbol=symbol,
            code=code,
            market=market,
            name=symbol,
            instrument_type="stock",
            list_date=list_date,
            delist_date=delist_date,
            is_active=active,
            price_tick=0.01,
            volume_multiple=1,
            raw_json=None,
        )

    repository.upsert_instruments(
        [
            instrument("600000.SH", 19991110, None, active=True),
            instrument("600001.SH", 19980122, 20170214, active=False),
            instrument("600002.SH", 20270101, None, active=False),
            instrument("600003.SH", 19990101, 20091231, active=False),
        ]
    )

    assert repository.stock_symbols_for_scope(20100101, 20260717) == [
        "600000.SH",
        "600001.SH",
    ]


def test_empty_vendor_refresh_preserves_official_delisted_metadata(tmp_path: Path) -> None:
    repository = CatalogRepository(StorageConfig(root=tmp_path))
    repository.initialize()
    official = InstrumentRecord(
        symbol="600001.SH",
        code="600001",
        market="SH",
        name="邯郸钢铁",
        instrument_type="stock",
        list_date=19980122,
        delist_date=20091229,
        is_active=False,
        price_tick=None,
        volume_multiple=None,
        raw_json='{"exchange":"SSE"}',
        source="sse_official_delisted_v1",
    )
    empty_vendor = InstrumentRecord(
        symbol="600001.SH",
        code="600001",
        market="SH",
        name=None,
        instrument_type="stock",
        list_date=None,
        delist_date=None,
        is_active=False,
        price_tick=None,
        volume_multiple=None,
        raw_json="{}",
        source="xtdata",
    )

    repository.upsert_instruments([official])
    repository.upsert_instruments([empty_vendor])

    stored = repository.instrument("600001.SH")
    assert stored is not None
    assert stored["name"] == "邯郸钢铁"
    assert stored["list_date"] == 19980122
    assert stored["delist_date"] == 20091229
    assert stored["source"] == "sse_official_delisted_v1"


def test_clean_retry_resolves_prior_hard_quality_error(tmp_path: Path) -> None:
    repository = CatalogRepository(StorageConfig(root=tmp_path))
    repository.initialize()
    repository.record_issues(
        [
            QualityIssueRecord(
                "error",
                "000974.SH",
                "1d",
                20130803,
                "not_open_trading_day",
                {},
                job_id="old-job",
            ),
            QualityIssueRecord(
                "error",
                "000974.SH",
                "1d",
                20130803,
                "current-error",
                {},
                job_id="current-job",
            ),
        ]
    )

    assert repository.resolve_open_errors("000974.SH", "1d", except_job_id="current-job") == 1
    with repository.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT job_id, resolved_at FROM data_quality_issues ORDER BY issue_id"
        ).fetchall()
    assert rows[0][1] is not None
    assert rows[1][1] is None


def test_precise_empty_result_resolves_superseded_placeholder_warning(
    tmp_path: Path,
) -> None:
    repository = CatalogRepository(StorageConfig(root=tmp_path))
    repository.initialize()
    repository.record_issues(
        [
            QualityIssueRecord(
                "warning",
                "395005.SZ",
                "1d",
                20100104,
                "qmt_etf_placeholder_without_listed_history",
                {},
                job_id="old-job",
            )
        ]
    )

    assert (
        repository.resolve_open_issues(
            "395005.SZ",
            "1d",
            rules=("qmt_etf_placeholder_without_listed_history",),
            except_job_id="current-job",
        )
        == 1
    )
    with repository.connect(readonly=True) as connection:
        resolved_at = connection.execute("SELECT resolved_at FROM data_quality_issues").fetchone()[
            0
        ]
    assert resolved_at is not None


def test_minute_shard_routing_and_upsert_are_cross_month_and_idempotent(
    tmp_path: Path,
) -> None:
    storage = StorageConfig(root=tmp_path)
    manager = ShardManager(storage)
    repository = BarRepository(storage)
    first = bar("600000.SH", "20260630150000", 10.0, source_hash="first")
    second = bar("600000.SH", "20260701093100", 10.1, source_hash="second")

    initial = repository.upsert("1m", [first, second])
    repeated = repository.upsert("1m", [first, second])

    assert initial.inserted_rows == 2
    assert repeated.inserted_rows == 0
    assert repeated.updated_rows == 0
    assert repeated.unchanged_rows == 2
    assert manager.minute_path("SH", "202606").exists()
    assert manager.minute_path("SH", "202607").exists()
    queried = repository.query(["600000.SH"], period="1m", start_date=20260630, end_date=20260701)
    assert queried["close"].tolist() == [10.0, 10.1]

    revised = bar("600000.SH", "20260701093100", 10.2, source_hash="revised")
    changed = repository.upsert("1m", [revised])
    assert changed.updated_rows == 1
    assert repository.query(["600000.SH"], period="1m", start_date=20260701, end_date=20260701)[
        "close"
    ].tolist() == [10.2]


def test_daily_data_uses_one_database_and_rejects_unknown_market(tmp_path: Path) -> None:
    storage = StorageConfig(root=tmp_path)
    repository = BarRepository(storage)
    result = repository.upsert("1d", [bar("430047.BJ", "20260717000000", 18.2, source_hash="bj")])
    assert result.shards == (tmp_path / "bars_1d.db",)
    assert repository.query(["430047.BJ"], period="1d", start_date=20260717, end_date=20260717)[
        "symbol"
    ].tolist() == ["430047.BJ"]


def test_reference_snapshots_replace_stale_memberships(tmp_path: Path) -> None:
    repository = CatalogRepository(StorageConfig(root=tmp_path))
    repository.initialize()

    repository.replace_sector_snapshots(
        20260717,
        [("sample", ["600000.SH", "000001.SZ"], 3)],
        source="test",
    )
    repository.replace_sector_snapshots(
        20260717,
        [("sample", ["600000.SH"], 2)],
        source="test",
    )
    repository.replace_index_weight_snapshots(
        20260717,
        [("000300.SH", {"600000.SH": 0.5}, 300)],
        source="test",
    )

    summary = repository.reference_data_summary(20260717)
    assert summary["sector_count"] == 1
    assert summary["a_share_sector_memberships"] == 1
    assert summary["index_count"] == 1
    assert summary["a_share_index_components"] == 1
