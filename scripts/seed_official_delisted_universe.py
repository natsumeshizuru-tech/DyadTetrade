"""Seed historical SSE/SZSE delisted A-shares before an xtdata backfill.

The QMT current-sector snapshot does not contain securities that have already left the
exchange.  Exchange lists are used only to discover symbols and listing lifecycles; bars,
corporate actions, and financial statements continue to come from the local xtdata client.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dyadtetrade.data.config import load_data_config
from dyadtetrade.data.models import InstrumentRecord
from dyadtetrade.data.sqlite import CatalogRepository

CHINA_TZ = ZoneInfo("Asia/Shanghai")
SSE_PAGE = "https://www.sse.com.cn/assortment/stock/list/delisting/"
SSE_API = "https://query.sse.com.cn/commonQuery.do"
SZSE_PAGE = "https://www.szse.cn/market/stock/suspend/index.html"
SZSE_API = "https://www.szse.cn/api/report/ShowReport/data"


@dataclass(frozen=True, slots=True)
class OfficialDelistedRecord:
    symbol: str
    name: str
    list_date: int
    delist_date: int
    exchange: str
    source_url: str
    raw: dict[str, Any]


def _request_json(url: str, *, referer: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/javascript,*/*;q=0.8",
            "Referer": referer,
            "User-Agent": "Mozilla/5.0 DyadResearchDataAudit/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read().decode("utf-8-sig")
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        match = re.search(r"\A[^({]*\((.*)\)\s*;?\s*\Z", payload, re.DOTALL)
        if match is None:
            raise
        return json.loads(match.group(1))


def _date(value: object) -> int | None:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 8:
        return None
    parsed = int(digits[:8])
    return parsed if 19900101 <= parsed <= 99991231 else None


def fetch_sse() -> list[OfficialDelistedRecord]:
    result: list[OfficialDelistedRecord] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "jsonCallBack": "jsonpCallback",
                "isPagination": "true",
                "pageHelp.pageSize": "2000",
                "pageHelp.pageNo": str(page),
                "pageHelp.beginPage": str(page),
                "pageHelp.cacheSize": "1",
                "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_ZZGP_L",
            }
        )
        payload = _request_json(f"{SSE_API}?{query}", referer=SSE_PAGE)
        rows = list(payload.get("result") or payload.get("pageHelp", {}).get("data") or [])
        for raw in rows:
            code = str(raw.get("A_STOCK_CODE") or "").strip()
            listed = _date(raw.get("LIST_DATE"))
            delisted = _date(raw.get("DELIST_DATE"))
            if not re.fullmatch(r"6\d{5}", code) or listed is None or delisted is None:
                continue
            result.append(
                OfficialDelistedRecord(
                    symbol=f"{code}.SH",
                    name=str(raw.get("COMPANY_ABBR") or raw.get("SEC_NAME_CN") or code),
                    list_date=listed,
                    delist_date=delisted,
                    exchange="SSE",
                    source_url=SSE_PAGE,
                    raw=dict(raw),
                )
            )
        page_help = payload.get("pageHelp") or {}
        page_count = int(page_help.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return result


def fetch_szse() -> list[OfficialDelistedRecord]:
    result: list[OfficialDelistedRecord] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "SHOWTYPE": "JSON",
                "CATALOGID": "1793_ssgs",
                "TABKEY": "tab2",
                "PAGENO": str(page),
            }
        )
        payload = _request_json(f"{SZSE_API}?{query}", referer=SZSE_PAGE)
        tab = next(
            (item for item in payload if str(item.get("metadata", {}).get("tabkey")) == "tab2"),
            None,
        )
        if tab is None:
            raise RuntimeError("SZSE official response omitted the terminated-list tab")
        for raw in tab.get("data") or []:
            code = str(raw.get("zqdm") or "").strip()
            listed = _date(raw.get("ssrq"))
            delisted = _date(raw.get("zzrq"))
            if not re.fullmatch(r"(?:0|2|3)\d{5}", code) or listed is None or delisted is None:
                continue
            result.append(
                OfficialDelistedRecord(
                    symbol=f"{code}.SZ",
                    name=str(raw.get("zqjc") or code),
                    list_date=listed,
                    delist_date=delisted,
                    exchange="SZSE",
                    source_url=SZSE_PAGE,
                    raw=dict(raw),
                )
            )
        metadata = tab.get("metadata") or {}
        page_count = int(metadata.get("pagecount") or 1)
        if page >= page_count:
            break
        page += 1
    return result


def scoped_records(
    records: list[OfficialDelistedRecord], start: int, end: int
) -> list[OfficialDelistedRecord]:
    selected: dict[str, OfficialDelistedRecord] = {}
    for record in records:
        if record.list_date > end or record.delist_date < start:
            continue
        current = selected.get(record.symbol)
        if current is None or (record.list_date, -record.delist_date) < (
            current.list_date,
            -current.delist_date,
        ):
            selected[record.symbol] = record
    return [selected[symbol] for symbol in sorted(selected)]


def _instrument(
    record: OfficialDelistedRecord, existing: dict[str, Any] | None
) -> InstrumentRecord:
    code, market = record.symbol.split(".")
    provenance = {
        "official_exchange_record": record.raw,
        "official_exchange": record.exchange,
        "official_url": record.source_url,
        "previous_raw_json": None if existing is None else existing.get("raw_json"),
    }
    return InstrumentRecord(
        symbol=record.symbol,
        code=code,
        market=market,
        name=record.name or (None if existing is None else existing.get("name")),
        instrument_type="stock",
        list_date=record.list_date,
        delist_date=record.delist_date,
        is_active=False,
        price_tick=None if existing is None else existing.get("price_tick"),
        volume_multiple=None if existing is None else existing.get("volume_multiple"),
        raw_json=json.dumps(provenance, ensure_ascii=False, sort_keys=True),
        source=f"{record.exchange.lower()}_official_delisted_v1",
    )


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/market_data.yaml"))
    parser.add_argument("--start", type=int, default=20100101)
    parser.add_argument("--end", type=int, default=20260717)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/data/official_delisted_universe_2010.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    official = [*fetch_sse(), *fetch_szse()]
    selected = scoped_records(official, args.start, args.end)
    if not selected:
        raise RuntimeError("official exchange requests returned no in-scope delisted A-shares")
    settings = load_data_config(args.config)
    catalog = CatalogRepository(settings.storage)
    catalog.initialize()
    existing = {record.symbol: catalog.instrument(record.symbol) for record in selected}
    new_symbols = [symbol for symbol, value in existing.items() if value is None]
    if not args.dry_run:
        catalog.upsert_instruments(
            [_instrument(record, existing[record.symbol]) for record in selected]
        )
    payload: dict[str, Any] = {
        "generated_at": datetime.now(CHINA_TZ).isoformat(),
        "scope": {"start": args.start, "end": args.end},
        "dry_run": bool(args.dry_run),
        "sources": {"SSE": SSE_PAGE, "SZSE": SZSE_PAGE},
        "official_delisted_all_dates": {
            "SSE": sum(record.exchange == "SSE" for record in official),
            "SZSE": sum(record.exchange == "SZSE" for record in official),
        },
        "selected_count": len(selected),
        "new_to_catalog_count": len(new_symbols),
        "existing_catalog_count": len(selected) - len(new_symbols),
        "records": [asdict(record) for record in selected],
    }
    _atomic_write(args.output, payload)
    summary = {key: value for key, value in payload.items() if key != "records"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
