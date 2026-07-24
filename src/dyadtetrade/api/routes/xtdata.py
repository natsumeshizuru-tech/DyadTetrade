from __future__ import annotations

from dataclasses import asdict
from datetime import date
from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from dyadtetrade.data.config import DataPlatformSettings, load_data_config
from dyadtetrade.data.downloader import BackfillRunner
from dyadtetrade.data.financial import FinancialBackfillRunner
from dyadtetrade.data.gateway import DataGatewayError
from dyadtetrade.data.reference_data import ReferenceDataSyncRunner
from dyadtetrade.data.xtdata_catalog import catalog_payload
from dyadtetrade.data.xtdata_gateway import (
    SubprocessXtDataGateway,
    XtDataGateway,
    build_xtdata_gateway,
)

router = APIRouter(prefix="/market/xtdata", tags=["xtdata"])
DatasetName = Literal[
    "history",
    "financial",
    "sector",
    "history_contracts",
    "holiday",
    "convertible_bond",
    "etf",
    "index_weight",
]


class XtDataDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: DatasetName = "history"
    symbols: list[str] = Field(default_factory=lambda: ["000001.SZ"], max_length=500)
    period: str = Field(default="1d", min_length=1, max_length=64)
    start_date: str = Field(default="20240101", pattern=r"^$|^\d{8}$")
    end_date: str = Field(default="", pattern=r"^$|^\d{8}$")
    tables: list[str] = Field(default_factory=list, max_length=32)
    import_to_shared_store: bool = True

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in values if item.strip()]
        for symbol in normalized:
            if not 3 <= len(symbol) <= 32 or "." not in symbol:
                raise ValueError(f"invalid XtData contract code: {symbol}")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_scope(self) -> XtDataDownloadRequest:
        if self.dataset in {"history", "financial"} and not self.symbols:
            raise ValueError(f"{self.dataset} requires at least one contract code")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        return self


def _settings() -> DataPlatformSettings:
    return load_data_config()


@lru_cache
def _gateway() -> XtDataGateway | SubprocessXtDataGateway:
    return build_xtdata_gateway(_settings().qmt)


def _capabilities() -> dict[str, Any]:
    try:
        return _gateway().capabilities()
    except DataGatewayError as exc:
        return {
            "connected": False,
            "sector_count": 0,
            "available_periods": [],
            "functions": {},
            "error": str(exc),
        }


@router.get("/catalog")
async def xtdata_catalog() -> dict[str, Any]:
    capabilities = await run_in_threadpool(_capabilities)
    return catalog_payload(capabilities)


@router.post("/download")
async def xtdata_download(request: XtDataDownloadRequest) -> dict[str, Any]:
    try:
        return await run_in_threadpool(_run_download, request)
    except (DataGatewayError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _run_download(request: XtDataDownloadRequest) -> dict[str, Any]:
    settings = _settings()
    gateway = _gateway()
    end = request.end_date or date.today().strftime("%Y%m%d")
    imported = False
    summary: dict[str, Any] = {}

    if request.import_to_shared_store and request.dataset == "history":
        if request.period not in {"1d", "1m"}:
            gateway.download_dataset(
                "history",
                symbols=request.symbols,
                period=request.period,
                start=request.start_date,
                end=end,
            )
            summary = {
                "cache_only": True,
                "reason": "shared SQLite stores raw 1d/1m; this period remains in MiniQMT cache",
            }
        else:
            backfill_result = BackfillRunner(settings, gateway).run(
                start=int(request.start_date),
                end=int(end),
                periods=[request.period],
                sectors=settings.universe.sectors,
                include_delisted=settings.universe.include_delisted,
                job_type="frontend-xtdata",
                symbols_override=request.symbols,
            )
            summary = asdict(backfill_result)
            imported = True
    elif request.import_to_shared_store and request.dataset == "financial":
        configured = settings
        if request.tables:
            configured = settings.model_copy(
                update={
                    "financial": settings.financial.model_copy(update={"tables": request.tables})
                }
            )
        financial_result = FinancialBackfillRunner(configured, gateway).run(
            start=int(request.start_date),
            end=int(end),
            symbols_override=request.symbols,
        )
        summary = asdict(financial_result)
        imported = True
    elif request.import_to_shared_store and request.dataset in {"sector", "index_weight"}:
        reference_result = ReferenceDataSyncRunner(settings, gateway).run(snapshot_date=int(end))
        summary = asdict(reference_result)
        imported = True
    else:
        gateway.download_dataset(
            request.dataset,
            symbols=request.symbols,
            period=request.period,
            start=request.start_date,
            end=end,
            tables=request.tables,
        )
        summary = {"cache_only": True}

    return {
        "status": "completed",
        "dataset": request.dataset,
        "period": request.period if request.dataset == "history" else None,
        "symbols": request.symbols,
        "downloaded": True,
        "imported_to_shared_store": imported,
        "shared_store": str(settings.storage.root.resolve()),
        "summary": summary,
    }
