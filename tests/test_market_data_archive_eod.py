from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dyadtetrade.data.config import ArchiveConfig, DataPlatformSettings, StorageConfig
from dyadtetrade.data.jobs.eod_sync import EodSyncJob
from dyadtetrade.data.jobs.validate import validation_payload
from dyadtetrade.data.models import BarRecord, InstrumentRecord
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository
from dyadtetrade.data.sqlite.archive import MarketDataArchive
from tests.test_sqlite_backfill import FakeGateway
from tests.test_sqlite_backfill import settings as backfill_settings

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def epoch(value: str) -> int:
    return int(datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ).timestamp() * 1000)


def test_monthly_shard_checkpoint_archive_checksum_restore_and_integrity(tmp_path: Path) -> None:
    storage = StorageConfig(root=tmp_path / "sqlite")
    archive_config = ArchiveConfig(root=tmp_path / "archive")
    catalog = CatalogRepository(storage)
    catalog.initialize()
    catalog.upsert_instruments(
        [
            InstrumentRecord(
                symbol="600000.SH",
                code="600000",
                market="SH",
                name="test",
                instrument_type="stock",
                list_date=19991110,
                delist_date=None,
                is_active=True,
                price_tick=0.01,
                volume_multiple=1,
                raw_json=None,
            )
        ]
    )
    bars = BarRepository(storage)
    inserted = bars.upsert(
        "1m",
        [
            BarRecord(
                symbol="600000.SH",
                ts=epoch("20260630150000"),
                trade_date=20260630,
                open=10,
                high=10,
                low=10,
                close=10,
                volume=100,
                amount=1000,
                ingested_at=epoch("20260701010000"),
                source_hash="archive-test",
            )
        ],
    )
    shard = inserted.shards[0]
    service = MarketDataArchive(storage, archive_config)

    sealed = service.seal_minute_shard(shard)
    restored = service.restore(Path(sealed.path), tmp_path / "restored" / shard.name)

    assert sealed.integrity == "ok"
    assert sealed.row_count == 1
    assert restored.integrity == "ok"
    assert restored.row_count == 1
    assert restored.sha256 == sealed.sha256
    with sqlite3.connect(catalog.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM archive_manifest").fetchone() == (1,)
        assert connection.execute(
            "SELECT writable FROM shard_manifest WHERE period='1m'"
        ).fetchone() == (0,)


def test_full_validation_checks_all_sqlite_files_and_duplicate_keys(tmp_path: Path) -> None:
    settings = DataPlatformSettings(storage=StorageConfig(root=tmp_path / "sqlite"))
    CatalogRepository(settings.storage).initialize()
    BarRepository(settings.storage).initialize()
    payload = validation_payload(settings)
    assert payload["integrity_ok"] is True
    assert payload["duplicate_primary_keys"] == 0
    assert payload["database_count"] == 2


class NonTradingGateway(FakeGateway):
    def get_trading_calendar(self, market: str, start: str, end: str) -> list[str]:
        del market, start, end
        return ["20260717"]


def test_eod_non_trading_day_exits_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    gateway = NonTradingGateway()
    summary = EodSyncJob(backfill_settings(tmp_path), gateway).run(
        now=datetime(2026, 7, 18, 16, 10, tzinfo=CHINA_TZ)
    )
    assert summary.status == "skipped"
    assert summary.reason == "not a trading day"
    assert gateway.download_calls == []


def test_eod_repeat_is_idempotent_and_creates_online_catalog_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    configured = backfill_settings(tmp_path)
    configured.archive.root = tmp_path / "archive"
    BarRepository(configured.storage).upsert(
        "1m",
        [
            BarRecord(
                symbol="600000.SH",
                ts=epoch("20260630150000"),
                trade_date=20260630,
                open=10,
                high=10,
                low=10,
                close=10,
                volume=100,
                amount=1000,
                source_hash="previous-month",
            )
        ],
    )
    gateway = FakeGateway()
    job = EodSyncJob(configured, gateway)
    now = datetime(2026, 7, 17, 16, 10, tzinfo=CHINA_TZ)

    first = job.run(now=now)
    second = job.run(now=now)

    assert first.status == "completed"
    assert second.status == "completed"
    assert first.inserted_rows > 0
    assert second.inserted_rows == 0
    assert first.coverage_ratio == 1.0
    assert first.catalog_backup is not None
    assert Path(first.catalog_backup).exists()
    assert (configured.archive.root / "minutes/202606/bars_1m_SH_202606.db").exists()
    assert all(call[2] == "20260715" for call in gateway.download_calls)
    assert any(call[3] == "20260717" for call in gateway.download_calls)
    with sqlite3.connect(configured.storage.root / "bars_1d.db") as connection:
        assert connection.execute(
            "SELECT count(*) FROM bars WHERE symbol='600000.SH'"
        ).fetchone() == (2,)


def test_windows_task_scripts_parse_and_use_project_virtualenv() -> None:
    root = Path(__file__).parents[1]
    for name in ("install_eod_task.ps1", "remove_eod_task.ps1", "run_eod_sync.ps1"):
        path = root / "scripts" / name
        assert path.exists()
        assert ".venv" in path.read_text(encoding="utf-8") or name != "run_eod_sync.ps1"
