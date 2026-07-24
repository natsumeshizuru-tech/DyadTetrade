from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dyadtetrade.data.config import DataPlatformSettings
from dyadtetrade.data.downloader import BackfillRunner
from dyadtetrade.data.gateway import MarketDataGateway
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository
from dyadtetrade.data.sqlite.archive import MarketDataArchive
from dyadtetrade.data.sqlite.connection import checkpoint, connect_sqlite, integrity_check

CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class EodSummary:
    status: str
    target_date: int | None
    reason: str | None
    coverage_ratio: float | None
    inserted_rows: int
    updated_rows: int
    failed_items: int
    catalog_backup: str | None
    elapsed_seconds: float
    report_json: str


class EodSyncJob:
    def __init__(self, settings: DataPlatformSettings, gateway: MarketDataGateway) -> None:
        self.settings = settings
        self.gateway = gateway
        self.catalog = CatalogRepository(settings.storage)
        self.bars = BarRepository(settings.storage)
        self.archive = MarketDataArchive(settings.storage, settings.archive)

    def run(self, *, now: datetime | None = None) -> EodSummary:
        started = time.monotonic()
        local_now = (now or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ)
        self.catalog.initialize()
        owner = uuid.uuid4().hex
        if not self.catalog.acquire_lock("eod-sync", owner, ttl_seconds=6 * 60 * 60):
            return self._write_summary(
                status="skipped",
                target_date=None,
                reason="another eod-sync instance is running",
                coverage_ratio=None,
                inserted_rows=0,
                updated_rows=0,
                failed_items=0,
                catalog_backup=None,
                started=started,
            )
        try:
            today = int(local_now.strftime("%Y%m%d"))
            lookback = int((local_now.date() - timedelta(days=30)).strftime("%Y%m%d"))
            days = [
                int(value)
                for value in self.gateway.get_trading_calendar("SH", str(lookback), str(today))
                if str(value).isdigit()
            ]
            if today not in days:
                return self._write_summary(
                    status="skipped",
                    target_date=today,
                    reason="not a trading day",
                    coverage_ratio=None,
                    inserted_rows=0,
                    updated_rows=0,
                    failed_items=0,
                    catalog_backup=None,
                    started=started,
                )
            overlap_count = max(1, self.settings.backfill.overlap_trading_days)
            overlap = sorted(days)[-overlap_count:]
            start = overlap[0]
            backfill = BackfillRunner(self.settings, self.gateway).run(
                start=start,
                end=today,
                periods=["1d", "1m"],
                sectors=self.settings.universe.sectors,
                include_delisted=self.settings.universe.include_delisted,
                job_type="eod-sync",
            )
            active = self.catalog.active_instrument_count()
            covered = self.bars.distinct_symbols_on_date("1d", today)
            coverage = covered / active if active else 0.0
            self._optimize_and_checkpoint()
            if self.settings.archive.seal_previous_month:
                self.archive.seal_previous_month(today)
            backup = self.archive.backup_catalog(today)
            status = (
                "completed"
                if backfill.failed_items == 0
                and coverage >= self.settings.scheduler.minimum_coverage_ratio
                else "degraded"
            )
            return self._write_summary(
                status=status,
                target_date=today,
                reason=None,
                coverage_ratio=coverage,
                inserted_rows=backfill.inserted_rows,
                updated_rows=backfill.updated_rows,
                failed_items=backfill.failed_items,
                catalog_backup=backup.path,
                started=started,
            )
        finally:
            self.catalog.release_lock("eod-sync", owner)

    def _optimize_and_checkpoint(self) -> None:
        for path in sorted(self.settings.storage.root.glob("*.db")):
            with connect_sqlite(path, wal=self.settings.storage.wal) as connection:
                if integrity_check(connection) != "ok":
                    raise RuntimeError(f"database integrity failed before EOD archive: {path}")
                connection.execute("PRAGMA optimize")
                checkpoint(connection)

    def _write_summary(
        self,
        *,
        status: str,
        target_date: int | None,
        reason: str | None,
        coverage_ratio: float | None,
        inserted_rows: int,
        updated_rows: int,
        failed_items: int,
        catalog_backup: str | None,
        started: float,
    ) -> EodSummary:
        elapsed = round(time.monotonic() - started, 3)
        output = Path("reports/data/eod")
        output.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(CHINA_TZ).strftime("%Y%m%dT%H%M%S")
        path = output / f"eod_{stamp}.json"
        summary = EodSummary(
            status,
            target_date,
            reason,
            coverage_ratio,
            inserted_rows,
            updated_rows,
            failed_items,
            catalog_backup,
            elapsed,
            str(path),
        )
        path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
