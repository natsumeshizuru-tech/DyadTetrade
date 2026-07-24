from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dyadtetrade.data.config import DataPlatformSettings
from dyadtetrade.data.gateway import DataGatewayError, MarketDataGateway
from dyadtetrade.data.sqlite import CatalogRepository
from dyadtetrade.data.sqlite.connection import integrity_check

CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class ReferenceDataSummary:
    job_id: str
    snapshot_date: int
    sector_count: int
    sector_memberships: int
    index_count: int
    index_components: int
    failed_items: int
    coverage: dict[str, object]
    elapsed_seconds: float
    report_json: str
    report_text: str


class ReferenceDataSyncRunner:
    def __init__(self, settings: DataPlatformSettings, gateway: MarketDataGateway) -> None:
        self.settings = settings
        self.gateway = gateway
        self.catalog = CatalogRepository(settings.storage)

    def run(self, *, snapshot_date: int, resume: bool = False) -> ReferenceDataSummary:
        started = time.monotonic()
        self.catalog.initialize()
        active_symbols = set(self.catalog.active_stock_symbols())
        job_id = (
            f"reference-data-sync-{datetime.now(CHINA_TZ):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        )
        self.catalog.create_job(
            job_id,
            "reference-data-sync",
            requested_start=snapshot_date,
            requested_end=snapshot_date,
            effective_start=snapshot_date,
            effective_end=snapshot_date,
        )
        errors: list[dict[str, str]] = []
        sector_count = sector_memberships = index_count = index_components = 0

        self.gateway.refresh_sector_data()
        sector_names = self.gateway.get_sector_list()
        if not self.settings.reference_data.sync_all_sectors:
            sector_names = list(self.settings.reference_data.index_sectors)
        if resume:
            completed_sectors = self.catalog.sector_names_on_snapshot(snapshot_date)
            sector_names = [name for name in sector_names if name not in completed_sectors]
        batch_size = self.settings.reference_data.write_batch_items
        for batch_number, names in enumerate(_batches(sector_names, batch_size), start=1):
            snapshots: list[tuple[str, list[str], int]] = []
            for name in names:
                try:
                    members = self.gateway.get_stock_list_in_sector(name)
                except DataGatewayError as exc:
                    errors.append({"dataset": "sector", "item": name, "error": str(exc)})
                    continue
                a_shares = sorted(active_symbols.intersection(members))
                snapshots.append((name, a_shares, len(members)))
            stored, memberships = self.catalog.replace_sector_snapshots(
                snapshot_date, snapshots, source="xtdata_sector_current_v1"
            )
            sector_count += stored
            sector_memberships += memberships
            print(
                json.dumps(
                    {
                        "event": "reference_sector_progress",
                        "job_id": job_id,
                        "batch": batch_number,
                        "batches": len(_batches(sector_names, batch_size)),
                        "sectors_total": sector_count,
                        "memberships_total": sector_memberships,
                        "failures_total": len(errors),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )

        index_codes = set(self.settings.reference_data.extra_index_codes)
        for sector in self.settings.reference_data.index_sectors:
            try:
                index_codes.update(self.gateway.get_stock_list_in_sector(sector))
            except DataGatewayError as exc:
                errors.append({"dataset": "index_discovery", "item": sector, "error": str(exc)})
        try:
            self.gateway.download_index_weight()
        except DataGatewayError as exc:
            errors.append({"dataset": "index_weight_download", "item": "all", "error": str(exc)})
        ordered_codes = sorted(code for code in index_codes if code.endswith((".SH", ".SZ", ".BJ")))
        if resume:
            completed_indices = self.catalog.index_codes_on_snapshot(snapshot_date)
            ordered_codes = [code for code in ordered_codes if code not in completed_indices]
        for names in _batches(ordered_codes, batch_size):
            snapshots_weight: list[tuple[str, dict[str, float], int]] = []
            for code in names:
                try:
                    weights = self.gateway.get_index_weight(code)
                except DataGatewayError as exc:
                    errors.append({"dataset": "index_weight", "item": code, "error": str(exc)})
                    continue
                a_share_weights = {
                    symbol: weight for symbol, weight in weights.items() if symbol in active_symbols
                }
                snapshots_weight.append((code, a_share_weights, len(weights)))
            stored, components = self.catalog.replace_index_weight_snapshots(
                snapshot_date, snapshots_weight, source="xtdata_index_weight_current_v1"
            )
            index_count += stored
            index_components += components

        coverage = self.catalog.reference_data_summary(snapshot_date)
        with self.catalog.connect(readonly=True) as connection:
            catalog_integrity = integrity_check(connection)
        status = "completed" if not errors else "degraded"
        elapsed = round(time.monotonic() - started, 3)
        report_dir = Path("reports/data/jobs")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_json = report_dir / f"{job_id}.json"
        report_text = report_dir / f"{job_id}.txt"
        payload: dict[str, object] = {
            "job_id": job_id,
            "status": status,
            "snapshot_date": snapshot_date,
            "resume": resume,
            "sector_count": sector_count,
            "sector_memberships": sector_memberships,
            "index_count": index_count,
            "index_components": index_components,
            "failed_items": len(errors),
            "coverage": coverage,
            "catalog_integrity": catalog_integrity,
            "limitations": [
                "XtData exposes current sector membership, not historical constituent dates.",
                "XtData exposes the currently downloaded index weights, not historical weights.",
                "The installed QMT weight package returned no constituents for 899050.BJ.",
            ],
            "errors": errors,
            "elapsed_seconds": elapsed,
        }
        report_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report_text.write_text(_human_summary(payload), encoding="utf-8")
        self.catalog.finish_job(
            job_id,
            status=status,
            total_items=sector_count + index_count + len(errors),
            success_items=sector_count + index_count,
            failed_items=len(errors),
            inserted_rows=sector_memberships + index_components,
            updated_rows=0,
            summary=payload,
        )
        return ReferenceDataSummary(
            job_id=job_id,
            snapshot_date=snapshot_date,
            sector_count=sector_count,
            sector_memberships=sector_memberships,
            index_count=index_count,
            index_components=index_components,
            failed_items=len(errors),
            coverage=coverage,
            elapsed_seconds=elapsed,
            report_json=str(report_json),
            report_text=str(report_text),
        )


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


def _human_summary(payload: dict[str, object]) -> str:
    lines = [
        f"job_id: {payload['job_id']}",
        f"status: {payload['status']}",
        f"snapshot_date: {payload['snapshot_date']}",
        f"sectors/memberships: {payload['sector_count']}/{payload['sector_memberships']}",
        f"indices/components: {payload['index_count']}/{payload['index_components']}",
        f"failed_items: {payload['failed_items']}",
        f"elapsed_seconds: {payload['elapsed_seconds']}",
    ]
    return "\n".join(lines) + "\n"


def summary_dict(summary: ReferenceDataSummary) -> dict[str, object]:
    return asdict(summary)
