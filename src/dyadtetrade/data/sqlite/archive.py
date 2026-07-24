from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from dyadtetrade.data.config import ArchiveConfig, StorageConfig
from dyadtetrade.data.sqlite.catalog_repository import CatalogRepository
from dyadtetrade.data.sqlite.connection import checkpoint, connect_sqlite, integrity_check


@dataclass(frozen=True, slots=True)
class ArchiveVerification:
    path: str
    integrity: str
    row_count: int
    sha256: str
    file_size: int


class MarketDataArchive:
    def __init__(self, storage: StorageConfig, archive: ArchiveConfig) -> None:
        self.storage = storage
        self.archive = archive
        self.catalog = CatalogRepository(storage)

    def backup_catalog(self, trade_date: int) -> ArchiveVerification:
        source_path = self.catalog.path
        destination = self.archive.root / "catalog" / f"catalog_{trade_date:08d}.db"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".db.tmp")
        with connect_sqlite(source_path, wal=self.storage.wal) as source:
            checkpoint(source)
            with connect_sqlite(temporary, wal=False) as target:
                source.backup(target)
        temporary.replace(destination)
        verification = self.verify_file(destination, table="instruments")
        self.catalog.record_archive(
            source_path=source_path,
            archive_path=destination,
            dataset="catalog",
            market=None,
            period="catalog",
            start_date=trade_date,
            end_date=trade_date,
            row_count=verification.row_count,
            file_size=verification.file_size,
            sha256=verification.sha256,
            schema_version=1,
        )
        self._prune_catalog_backups()
        return verification

    def seal_minute_shard(self, shard: Path, *, vacuum: bool = False) -> ArchiveVerification:
        if not shard.exists():
            raise FileNotFoundError(shard)
        market, year_month = _minute_identity(shard)
        with connect_sqlite(shard, wal=self.storage.wal) as source:
            checkpoint(source)
            if integrity_check(source) != "ok":
                raise RuntimeError(f"cannot archive corrupt shard: {shard}")
            if vacuum:
                source.execute("VACUUM")
            row = source.execute(
                "SELECT count(*), min(trade_date), max(trade_date) FROM bars"
            ).fetchone()
            if row is None:
                raise RuntimeError(f"cannot inspect shard: {shard}")
            row_count = int(row[0])
            start_date = None if row[1] is None else int(row[1])
            end_date = None if row[2] is None else int(row[2])
        destination = self.archive.root / "minutes" / year_month / shard.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".db.tmp")
        shutil.copy2(shard, temporary)
        temporary.replace(destination)
        verification = self.verify_file(destination, table="bars", expected_rows=row_count)
        self.catalog.record_archive(
            source_path=shard,
            archive_path=destination,
            dataset="bars",
            market=market,
            period="1m",
            start_date=start_date,
            end_date=end_date,
            row_count=row_count,
            file_size=verification.file_size,
            sha256=verification.sha256,
            schema_version=1,
        )
        self.catalog.record_shard(
            shard,
            dataset="bars",
            market=market,
            period="1m",
            start_date=start_date,
            end_date=end_date,
            row_count=row_count,
            writable=False,
            checksum=verification.sha256,
        )
        return verification

    def seal_previous_month(self, target_date: int) -> tuple[ArchiveVerification, ...]:
        """Seal each existing market shard for the month preceding target_date."""
        parsed = date.fromisoformat(
            f"{str(target_date)[:4]}-{str(target_date)[4:6]}-{str(target_date)[6:8]}"
        )
        previous = parsed.replace(day=1) - timedelta(days=1)
        year_month = previous.strftime("%Y%m")
        verified: list[ArchiveVerification] = []
        for market in ("SH", "SZ", "BJ"):
            shard = self.storage.root / f"bars_1m_{market}_{year_month}.db"
            if not shard.exists():
                continue
            destination = self.archive.root / "minutes" / year_month / shard.name
            if destination.exists():
                verified.append(self.verify_file(destination, table="bars"))
            else:
                verified.append(self.seal_minute_shard(shard))
        return tuple(verified)

    def restore(self, archive_path: Path, target: Path) -> ArchiveVerification:
        source_verification = self.verify_file(archive_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        shutil.copy2(archive_path, temporary)
        temporary.replace(target)
        restored = self.verify_file(target, expected_rows=source_verification.row_count)
        if restored.sha256 != source_verification.sha256:
            raise RuntimeError("restored database checksum differs from archive")
        return restored

    def verify_file(
        self,
        path: Path,
        *,
        table: str | None = None,
        expected_rows: int | None = None,
    ) -> ArchiveVerification:
        if not path.exists():
            raise FileNotFoundError(path)
        with connect_sqlite(path, readonly=True) as connection:
            status = integrity_check(connection)
            if status != "ok":
                raise RuntimeError(f"SQLite integrity check failed for {path}: {status}")
            selected_table = table or _fact_table(connection)
            row_count = int(
                connection.execute(f"SELECT count(*) FROM {selected_table}").fetchone()[0]
            )
        if expected_rows is not None and row_count != expected_rows:
            raise RuntimeError(
                f"archive row count mismatch for {path}: {row_count} != {expected_rows}"
            )
        return ArchiveVerification(
            path=str(path),
            integrity=status,
            row_count=row_count,
            sha256=_sha256(path),
            file_size=path.stat().st_size,
        )

    def _prune_catalog_backups(self) -> None:
        directory = self.archive.root / "catalog"
        backups = sorted(directory.glob("catalog_*.db"), reverse=True)
        for path in backups[self.archive.retention_daily_catalog_days :]:
            path.unlink()


def _minute_identity(path: Path) -> tuple[str, str]:
    parts = path.stem.split("_")
    if len(parts) != 4 or parts[:2] != ["bars", "1m"]:
        raise ValueError(f"not a minute shard: {path}")
    market, year_month = parts[2], parts[3]
    if market not in {"SH", "SZ", "BJ"} or len(year_month) != 6:
        raise ValueError(f"invalid minute shard name: {path}")
    return market, year_month


def _fact_table(connection: sqlite3.Connection) -> str:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "bars" in tables:
        return "bars"
    if "instruments" in tables:
        return "instruments"
    raise RuntimeError("archive contains no recognized fact table")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
