from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from dyadtetrade.data.gateway import MarketDataGateway
from dyadtetrade.data.models import CorporateActionRecord, InstrumentRecord

A_SHARE = re.compile(r"^(?:\d{6}\.(?:SH|SZ)|(?:43|83|87|88)\d{4}\.BJ|920\d{3}\.BJ)$")


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    symbols: tuple[str, ...]
    current_symbols: frozenset[str]
    sectors: dict[str, tuple[str, ...]]
    instrument_types: dict[str, str]
    expired_sectors: tuple[str, ...]


def discover_universe(
    gateway: MarketDataGateway,
    *,
    sectors: list[str],
    extra_sectors: list[str],
    include_delisted: bool,
    existing_symbols: list[str] | None = None,
) -> UniverseSnapshot:
    available = gateway.get_sector_list()
    requested = [*sectors, *extra_sectors]
    expired = (
        [name for name in available if "过期" in name or "退市" in name] if include_delisted else []
    )
    members: dict[str, tuple[str, ...]] = {}
    instrument_types: dict[str, str] = {}
    current: set[str] = set()
    all_symbols = {item.upper() for item in existing_symbols or [] if A_SHARE.fullmatch(item)}
    for sector in [*requested, *expired]:
        if sector not in available:
            continue
        symbols = tuple(
            sorted(
                {
                    item.upper()
                    for item in gateway.get_stock_list_in_sector(sector)
                    if A_SHARE.fullmatch(item.upper())
                }
            )
        )
        members[sector] = symbols
        instrument_type = sector_instrument_type(sector)
        instrument_types.update({symbol: instrument_type for symbol in symbols})
        all_symbols.update(symbols)
        if sector in requested:
            current.update(symbols)
    return UniverseSnapshot(
        tuple(sorted(all_symbols)),
        frozenset(current),
        members,
        instrument_types,
        tuple(sorted(expired)),
    )


def instrument_from_xtdata(
    symbol: str,
    detail: dict[str, Any],
    *,
    is_current: bool,
    instrument_type: str = "stock",
) -> InstrumentRecord:
    code, market = symbol.split(".", 1)
    list_date = _date_value(detail.get("OpenDate") or detail.get("CreateDate"))
    delist_date = _date_value(detail.get("ExpireDate"))
    name_value = detail.get("InstrumentName") or detail.get("Name")
    # Cash indices commonly omit share-capital fields and OpenDate in QMT.
    # That shape is valid for an index and must not be treated as a future ETF.
    is_placeholder = instrument_type != "index" and is_unlaunched_placeholder(detail)
    if is_placeholder:
        list_date = _date_value(detail.get("CreateDate"))
    return InstrumentRecord(
        symbol=symbol,
        code=code,
        market=market,
        name=None if name_value is None else str(name_value),
        instrument_type=instrument_type,
        list_date=list_date,
        delist_date=delist_date,
        is_active=is_current and not is_placeholder,
        price_tick=_float_value(detail.get("PriceTick")),
        volume_multiple=_float_value(detail.get("VolumeMultiple")),
        raw_json=json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str),
    )


def is_unlaunched_placeholder(detail: dict[str, Any]) -> bool:
    open_date = str(detail.get("OpenDate") or "").strip()
    invalid_open_date = open_date in {"", "0", "00000000", "19700101"}
    return (
        invalid_open_date
        and (_float_value(detail.get("FloatVolume")) or 0) == 0
        and (_float_value(detail.get("TotalVolume")) or 0) == 0
    )


def sector_instrument_type(sector: str) -> str:
    normalized = sector.upper()
    if "ETF" in normalized:
        return "etf"
    if "指数" in sector:
        return "index"
    return "stock"


def actions_from_xtdata(symbol: str, frame: pd.DataFrame) -> list[CorporateActionRecord]:
    if frame.empty:
        return []
    normalized = frame.copy()
    normalized.columns = [str(column).replace("_", "").lower() for column in normalized.columns]
    if "actiondate" in normalized:
        raw_dates: list[object] = normalized.pop("actiondate").tolist()
    elif "time" in normalized:
        raw_dates = normalized.pop("time").tolist()
    else:
        raw_dates = list(normalized.index)
    result: list[CorporateActionRecord] = []
    for offset, (_, row) in enumerate(normalized.iterrows()):
        action_date = _date_value(raw_dates[offset])
        if action_date is None:
            continue
        payload = {str(key): value for key, value in row.items()}
        result.append(
            CorporateActionRecord(
                symbol=symbol,
                action_date=action_date,
                interest=_float_value(payload.get("interest")) or 0.0,
                stock_bonus=_float_value(payload.get("stockbonus")) or 0.0,
                stock_gift=_float_value(payload.get("stockgift")) or 0.0,
                allot_num=_float_value(payload.get("allotnum")) or 0.0,
                allot_price=_float_value(payload.get("allotprice")) or 0.0,
                gugai=int(_float_value(payload.get("gugai")) or 0),
                dr=_float_value(payload.get("dr")) or 1.0,
                raw_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            )
        )
    return result


def _date_value(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        magnitude = abs(float(value))
        if magnitude > 1_000_000_000:
            unit = "ms" if magnitude > 100_000_000_000 else "s"
            timestamp = pd.Timestamp(value, unit=unit, tz="UTC").tz_convert("Asia/Shanghai")
            return int(timestamp.strftime("%Y%m%d"))
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) in {10, 13}:
        unit = "ms" if len(digits) == 13 else "s"
        timestamp = pd.Timestamp(int(digits), unit=unit, tz="UTC").tz_convert("Asia/Shanghai")
        return int(timestamp.strftime("%Y%m%d"))
    if len(digits) < 8:
        return None
    parsed = int(digits[:8])
    return None if parsed <= 19700101 else parsed


def _float_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None
